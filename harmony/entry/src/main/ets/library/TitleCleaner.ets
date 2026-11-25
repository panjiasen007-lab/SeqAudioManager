// 标题清洗：去掉「01. / [01] / 01 - / 1 中文」等曲目序号前缀。
// 规则与桌面版 core/clean.py 对齐：只认 1~2 位数字，宁可漏删不误删。
const PATTERNS: RegExp[] = [
  new RegExp('^\\s*[\\[\\(（【〔]\\s*(\\d{1,3})\\s*[\\]\\)）】〕]\\s*'),
  new RegExp('^\\s*(\\d{1,3})\\s*[.、·\\-—_–:：]\\s*'),
  new RegExp('^\\s*(\\d{2,3})\\s+(?=\\S)'),
  new RegExp('^\\s*(\\d)\\s+(?=[\\u4e00-\\u9fff])')
];

export class TitleCleaner {
  static stripNumber(title: string): string {
    const text = title.trim();
    if (text == '') {
      return title;
    }
    for (const re of PATTERNS) {
      const m = re.exec(text);
      if (m == undefined || m == null) {
        continue;
      }
      if (m[1].length > 2) {
        // 3 位及以上更可能是年份/编号，不当作曲目序号
        continue;
      }
      const rest = text.substring(m[0].length).trim();
      if (rest == '') {
        return title;
      }
      return rest;
    }
    return title;
  }
}
