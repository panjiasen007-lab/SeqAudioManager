import fs from '@ohos.file.fs';
import taskpool from '@ohos.taskpool';

export class ImportReport {
  total: number = 0;
  ok: number = 0;
  skipped: number = 0;
  failed: string[] = [];
}

// 单文件拷贝放到 TaskPool：picker 返回的 file://docs/... URI 走文件管理服务，
// 首次读取可能阻塞数秒；放在主线程会触发 THREAD_BLOCK_6S 被系统强杀。
@Concurrent
function copyOne(srcUri: string, dest: string): number {
  let src: fs.File | undefined = undefined;
  let dst: fs.File | undefined = undefined;
  try {
    src = fs.openSync(srcUri, fs.OpenMode.READ_ONLY);
    dst = fs.openSync(dest, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
    const buf = new ArrayBuffer(1048576);
    let total = 0;
    let n = 0;
    do {
      n = fs.readSync(src.fd, buf, { offset: 0, length: 1048576 });
      if (n > 0) {
        let written = 0;
        while (written < n) {
          const w = fs.writeSync(dst.fd, buf, { offset: written, length: n - written });
          if (w <= 0) {
            return -1;
          }
          written += w;
        }
        total += n;
      }
    } while (n > 0);
    return total;
  } catch (e) {
    return -1;
  } finally {
    if (src !== undefined) {
      try {
        fs.closeSync(src);
      } catch (e) {
        // ignore
      }
    }
    if (dst !== undefined) {
      try {
        fs.closeSync(dst);
      } catch (e) {
        // ignore
      }
    }
  }
}

// 批量导入： picker URI → 应用沙箱；重复可选跳过/覆盖，失败逐条记录可重试
export class ImportManager {
  static async importFiles(uris: string[], destDir: string, overwrite: boolean): Promise<ImportReport> {
    const rep = new ImportReport();
    rep.total = uris.length;
    try {
      fs.mkdirSync(destDir, true);
    } catch (e) {
      // 目录已存在
    }
    for (const uri of uris) {
      try {
        let name = decodeURIComponent(uri.substring(uri.lastIndexOf('/') + 1));
        if (name == '') {
          rep.failed.push(uri);
          continue;
        }
        if (!name.includes('.')) {
          // 部分 URI 末尾带查询串，去掉后重新取文件名
          name = name.split('?')[0];
        }
        const dest = destDir.endsWith('/') ? destDir + name : `${destDir}/${name}`;
        if (!overwrite) {
          try {
            fs.statSync(dest);
            rep.skipped++;
            continue;
          } catch (e) {
            // 目标不存在，继续复制
          }
        }
        // docs URI 的句柄关闭可能长时间不返回：给每个文件 30s 上限，超时按失败处理，
        // 避免导入流程（以及 UI 的等待态）被单个坏句柄无限拖住。
        const task = taskpool.execute(copyOne, uri, dest);
        const written = await Promise.race([
          task as Promise<number>,
          new Promise<number>((resolve: (v: number) => void) => {
            setTimeout(() => {
              resolve(-2);
            }, 30000);
          })
        ]) as number;
        console.info(`import: ${name} bytes=${written}`);
        if (written > 0) {
          rep.ok++;
        } else {
          // 失败时清掉半截文件，避免下次扫描入库空壳
          try {
            fs.unlinkSync(dest);
          } catch (e) {
            // ignore
          }
          rep.failed.push(uri);
        }
      } catch (e) {
        rep.failed.push(uri);
      }
    }
    console.info(`import done: total=${rep.total} ok=${rep.ok} skipped=${rep.skipped} failed=${rep.failed.length}`);
    return rep;
  }
}
