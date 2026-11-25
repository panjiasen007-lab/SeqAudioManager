import fs from '@ohos.file.fs';
import util from '@ohos.util';

export class CueTrack {
  no: number = 0;
  title: string = '';
  performer: string = '';
  startMs: number = 0;
  endMs: number = 0;
  // INDEX 00：本轨预间隙起点，也是上一轨的结束边界
  pregapMs: number = 0;
}

export class CueFile {
  fileName: string = '';
  tracks: CueTrack[] = [];
}

export class CueSheet {
  album: string = '';
  albumPerformer: string = '';
  files: CueFile[] = [];
}

function unquote(s: string): string {
  const t = s.trim();
  if (t.length >= 2 && t.startsWith('"') && t.endsWith('"')) {
    return t.substring(1, t.length - 1);
  }
  return t;
}

function firstQuoted(s: string): string {
  const a = s.indexOf('"');
  const b = s.lastIndexOf('"');
  if (a >= 0 && b > a) {
    return s.substring(a + 1, b);
  }
  return unquote(s);
}

// MM:SS:FF，1 frame = 1/75 s
function indexToMs(s: string): number {
  const parts = s.trim().split(':');
  if (parts.length < 3) {
    return 0;
  }
  const mm = parseInt(parts[0]);
  const ss = parseInt(parts[1]);
  const ff = parseInt(parts[2]);
  if (isNaN(mm) || isNaN(ss) || isNaN(ff)) {
    return 0;
  }
  return Math.round((mm * 60 + ss + ff / 75) * 1000);
}

export class CueParser {
  static parseText(text: string): CueSheet {
    const sheet = new CueSheet();
    let albumPerformer = '';
    let currentFile: CueFile | undefined = undefined;
    let currentTrack: CueTrack | undefined = undefined;
    const lines = text.replace(/\r/g, '').split('\n');
    for (const raw of lines) {
      const line = raw.trim();
      if (line == '' || line.startsWith('REM')) {
        continue;
      }
      const sp = line.indexOf(' ');
      const cmd = (sp >= 0 ? line.substring(0, sp) : line).toUpperCase();
      const rest = sp >= 0 ? line.substring(sp + 1).trim() : '';
      if (cmd == 'PERFORMER') {
        const v = firstQuoted(rest);
        if (currentTrack) {
          currentTrack.performer = v;
        } else {
          albumPerformer = v;
        }
      } else if (cmd == 'TITLE') {
        const v = firstQuoted(rest);
        if (currentTrack) {
          currentTrack.title = v;
        } else {
          sheet.album = v;
        }
      } else if (cmd == 'FILE') {
        currentFile = new CueFile();
        currentFile.fileName = firstQuoted(rest);
        sheet.files.push(currentFile);
        currentTrack = undefined;
      } else if (cmd == 'TRACK') {
        if (!currentFile) {
          continue;
        }
        const m = rest.match(/^(\d+)\s+AUDIO/i);
        currentTrack = new CueTrack();
        currentTrack.no = m ? parseInt(m[1]) : currentFile.tracks.length + 1;
        currentFile.tracks.push(currentTrack);
      } else if (cmd == 'INDEX' && currentTrack) {
        const m = rest.match(/^(\d+)\s+([\d:]+)/);
        if (m && m[1] == '01') {
          currentTrack.startMs = indexToMs(m[2]);
        } else if (m && m[1] == '00') {
          currentTrack.pregapMs = indexToMs(m[2]);
        }
      }
    }
    sheet.albumPerformer = albumPerformer;
    // 轨 ending = 下一轨起点；最后一轨留 0，由播放器用物理时长兜底
    for (const f of sheet.files) {
      for (let i = 0; i < f.tracks.length - 1; i++) {
        f.tracks[i].endMs = f.tracks[i + 1].startMs;
      }
    }
    return sheet;
  }

  static parseFile(path: string): CueSheet {
    const st = fs.statSync(path);
    if (st.size <= 0 || st.size > 4 * 1024 * 1024) {
      return new CueSheet();
    }
    const f = fs.openSync(path, fs.OpenMode.READ_ONLY);
    try {
      const buf = new ArrayBuffer(st.size);
      const n = fs.readSync(f.fd, buf, { offset: 0, length: st.size });
      const bytes = new Uint8Array(buf, 0, n);
      let text = '';
      try {
        text = util.TextDecoder.create('utf-8').decodeWithStream(bytes);
      } catch (e) {
        try {
          text = util.TextDecoder.create('gbk').decodeWithStream(bytes);
        } catch (e2) {
          text = '';
        }
      }
      return CueParser.parseText(text);
    } finally {
      fs.closeSync(f);
    }
  }
}
