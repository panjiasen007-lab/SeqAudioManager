import fs from '@ohos.file.fs';

export class ImportReport {
  total: number = 0;
  ok: number = 0;
  skipped: number = 0;
  failed: string[] = [];
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
      let name = '';
      try {
        name = decodeURIComponent(uri.substring(uri.lastIndexOf('/') + 1));
        if (name == '') {
          rep.failed.push(uri);
          continue;
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
        const src = fs.openSync(uri, fs.OpenMode.READ_ONLY);
        const dst = fs.openSync(dest, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
        const buf = new ArrayBuffer(1048576);
        try {
          let n = 0;
          do {
            n = fs.readSync(src.fd, buf, { offset: 0, length: 1048576 });
            if (n > 0) {
              fs.writeSync(dst.fd, buf, { offset: 0, length: n });
            }
          } while (n > 0);
          rep.ok++;
        } finally {
          try {
            fs.closeSync(src);
          } catch (e) {
            // ignore
          }
          try {
            fs.closeSync(dst);
          } catch (e) {
            // ignore
          }
        }
      } catch (e) {
        rep.failed.push(uri);
      }
    }
    return rep;
  }
}
