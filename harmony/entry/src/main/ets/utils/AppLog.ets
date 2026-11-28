// 运行日志：桌面版底部「运行日志」栏的内存数据源。
// 只保留最近 300 行；订阅者用于 UI 刷新。
export class LogLine {
  seq: number = 0;
  time: string = '';
  level: string = '';
  message: string = '';

  constructor(seq: number, time: string, level: string, message: string) {
    this.seq = seq;
    this.time = time;
    this.level = level;
    this.message = message;
  }
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

export class AppLog {
  static lines: LogLine[] = [];
  private static seq: number = 0;
  private static listeners: (() => void)[] = [];

  static add(level: string, message: string): void {
    const d = new Date();
    const time = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
    AppLog.seq++;
    AppLog.lines.push(new LogLine(AppLog.seq, time, level, message));
    if (AppLog.lines.length > 300) {
      AppLog.lines.splice(0, AppLog.lines.length - 300);
    }
    for (const cb of AppLog.listeners) {
      try {
        cb();
      } catch (e) {
        // 订阅者异常不影响日志本身
      }
    }
    console.info(`[${level}] ${message}`);
  }

  static info(message: string): void {
    AppLog.add('INFO', message);
  }

  static warn(message: string): void {
    AppLog.add('WARN', message);
  }

  static error(message: string): void {
    AppLog.add('ERROR', message);
  }

  static clear(): void {
    AppLog.lines = [];
    for (const cb of AppLog.listeners) {
      try {
        cb();
      } catch (e) {
        // ignore
      }
    }
  }

  static subscribe(cb: () => void): void {
    AppLog.listeners.push(cb);
  }

  static unsubscribe(cb: () => void): void {
    const i = AppLog.listeners.indexOf(cb);
    if (i >= 0) {
      AppLog.listeners.splice(i, 1);
    }
  }

  static text(): string {
    const out: string[] = [];
    for (const line of AppLog.lines) {
      out.push(`${line.time}  ${line.message}`);
    }
    return out.join('\n');
  }
}
