import http from '@ohos.net.http';
import { BusinessError } from '@ohos.base';

const TIMEOUT_MS: number = 5000;
const MAX_CONCURRENT: number = 3;

// 统一网络入口：超时 5s、失败重试 1 次、并发上限 3
export class HttpStack {
  private static active: number = 0;

  private static async acquire(): Promise<void> {
    while (HttpStack.active >= MAX_CONCURRENT) {
      await new Promise<void>((resolve: () => void) => {
        setTimeout(resolve, 20);
      });
    }
    HttpStack.active++;
  }

  private static release(): void {
    if (HttpStack.active > 0) {
      HttpStack.active--;
    }
  }

  private static async once(url: string, type: http.HttpDataType,
    headers?: Record<string, string>, timeoutMs: number = TIMEOUT_MS): Promise<Object> {
    await HttpStack.acquire();
    const req = http.createHttp();
    try {
      const resp: http.HttpResponse = await req.request(url, {
        method: http.RequestMethod.GET,
        expectDataType: type,
        connectTimeout: timeoutMs,
        readTimeout: timeoutMs,
        header: headers
      });
      if (resp.responseCode < 200 || resp.responseCode >= 300) {
        throw new Error(`HTTP ${resp.responseCode}`);
      }
      return resp.result as Object;
    } finally {
      req.destroy();
      HttpStack.release();
    }
  }

  private static async withRetry(url: string, type: http.HttpDataType,
    headers?: Record<string, string>, timeoutMs: number = TIMEOUT_MS): Promise<Object> {
    try {
      return await HttpStack.once(url, type, headers, timeoutMs);
    } catch (e) {
      return await HttpStack.once(url, type, headers, timeoutMs);
    }
  }

  static async getText(url: string): Promise<string> {
    const r = await HttpStack.withRetry(url, http.HttpDataType.STRING);
    return r as string;
  }

  static async getTextEx(url: string, headers?: Record<string, string>,
    timeoutMs: number = TIMEOUT_MS): Promise<string> {
    const r = await HttpStack.withRetry(url, http.HttpDataType.STRING, headers, timeoutMs);
    return r as string;
  }

  static async getBytes(url: string): Promise<Uint8Array> {
    const r = await HttpStack.withRetry(url, http.HttpDataType.ARRAY_BUFFER);
    const ab = r as ArrayBuffer;
    return new Uint8Array(ab);
  }

  static async getBytesEx(url: string, headers?: Record<string, string>,
    timeoutMs: number = TIMEOUT_MS): Promise<Uint8Array> {
    const r = await HttpStack.withRetry(url, http.HttpDataType.ARRAY_BUFFER, headers, timeoutMs);
    const ab = r as ArrayBuffer;
    return new Uint8Array(ab);
  }
}
