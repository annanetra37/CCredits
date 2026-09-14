// Shared look for the CCredits documentation set.
// The colours are the portal's own validated palette, so a printed page and
// the screen read as the same product.
const d = require('docx');

const C = {
  ink:      '0B0B0B',
  ink2:     '52514E',
  ink3:     '7A7873',
  accent:   '256ABF',   // interactive blue
  blue:     '2A78D6',
  green:    '1BAF7A',   // eligible
  greenInk: '0A6E4C',
  gold:     'EDA100',   // suspect
  goldInk:  '8A5A00',
  red:      'D03B3B',   // gap / critical
  redInk:   'A32626',
  violet:   '4A3AA7',
  violetInk:'3A2D85',
  wash:     'F4F7FC',
  washGreen:'E4F6EF',
  washGold: 'FDF3DE',
  washRed:  'FBEAEA',
  washViol: 'EEEBFA',
  rule:     'E3E2DD',
  band:     'F7F7F5',
  code:     'F4F4F1',
};

const W = 9360;               // content width in DXA at 1" margins on A4

const t = (text, o = {}) => new d.TextRun({ text, ...o });

const P = (text, o = {}) => new d.Paragraph({
  children: Array.isArray(text) ? text : [t(text, { size: o.size || 20, color: o.color || C.ink2, bold: o.bold, italics: o.italics, font: o.font })],
  spacing: { before: o.before ?? 60, after: o.after ?? 120, line: o.line ?? 280 },
  alignment: o.align,
  indent: o.indent,
  border: o.border,
  shading: o.shading,
});

const spacer = (h = 120) => new d.Paragraph({ text: '', spacing: { after: h } });

// A title block: big name, coloured rule, standfirst.
const titleBlock = (kicker, title, standfirst, colour) => ([
  new d.Paragraph({
    children: [t(kicker.toUpperCase(), { size: 17, bold: true, color: colour, characterSpacing: 40 })],
    spacing: { after: 70 },
  }),
  new d.Paragraph({
    children: [t(title, { size: 46, bold: true, color: C.ink })],
    spacing: { after: 130 },
  }),
  new d.Paragraph({
    text: '',
    border: { bottom: { style: d.BorderStyle.SINGLE, size: 20, color: colour, space: 1 } },
    spacing: { after: 170 },
  }),
  new d.Paragraph({
    children: [t(standfirst, { size: 22, color: C.ink2 })],
    spacing: { after: 260, line: 300 },
  }),
]);

const h1 = (text, colour = C.accent) => new d.Paragraph({
  heading: d.HeadingLevel.HEADING_1,
  children: [t(text, { size: 30, bold: true, color: colour })],
  spacing: { before: 380, after: 140 },
  border: { bottom: { style: d.BorderStyle.SINGLE, size: 10, color: colour, space: 6 } },
});

const h2 = (text, colour = C.ink) => new d.Paragraph({
  heading: d.HeadingLevel.HEADING_2,
  children: [t(text, { size: 24, bold: true, color: colour })],
  spacing: { before: 270, after: 100 },
});

const h3 = (text, colour = C.ink2) => new d.Paragraph({
  heading: d.HeadingLevel.HEADING_3,
  children: [t(text, { size: 21, bold: true, color: colour })],
  spacing: { before: 190, after: 80 },
});

const bullet = (text, level = 0) => new d.Paragraph({
  children: Array.isArray(text) ? text : [t(text, { size: 20, color: C.ink2 })],
  numbering: { reference: 'dots', level },
  spacing: { before: 40, after: 70, line: 280 },
});

// Each separate ordered list needs its own instance, or the numbering runs on
// from the previous list instead of restarting at 1.
const numbered = (text, instance = 0, level = 0) => new d.Paragraph({
  children: Array.isArray(text) ? text : [t(text, { size: 20, color: C.ink2 })],
  numbering: { reference: 'steps', level, instance },
  spacing: { before: 40, after: 70, line: 280 },
});

const mono = (text, o = {}) => t(text, { font: 'Consolas', size: o.size || 18, color: o.color || C.violetInk, bold: o.bold });

// A shaded code block. Each line is its own paragraph — \n is not allowed.
const code = (lines, colour = C.code) => lines.map((line, i) => new d.Paragraph({
  children: [mono(line || ' ', { color: C.ink })],
  shading: { type: d.ShadingType.CLEAR, fill: colour },
  spacing: { before: i === 0 ? 100 : 0, after: i === lines.length - 1 ? 150 : 0, line: 250 },
  indent: { left: 140, right: 140 },
  border: {
    left: { style: d.BorderStyle.SINGLE, size: 12, color: C.accent, space: 6 },
  },
}));

// A coloured callout: a bold lead-in, then the body, on a tinted panel.
const callout = (lead, body, colour, fill) => {
  const cell = new d.TableCell({
    width: { size: W, type: d.WidthType.DXA },
    shading: { type: d.ShadingType.CLEAR, fill },
    margins: { top: 140, bottom: 140, left: 180, right: 180 },
    borders: {
      top:    { style: d.BorderStyle.NIL },
      bottom: { style: d.BorderStyle.NIL },
      right:  { style: d.BorderStyle.NIL },
      left:   { style: d.BorderStyle.SINGLE, size: 18, color: colour },
    },
    children: [new d.Paragraph({
      children: [
        t(lead + '  ', { size: 20, bold: true, color: colour }),
        t(body, { size: 20, color: C.ink2 }),
      ],
      spacing: { line: 280 },
    })],
  });
  return new d.Table({
    width: { size: W, type: d.WidthType.DXA },
    columnWidths: [W],
    rows: [new d.TableRow({ children: [cell] })],
  });
};

// A table with a coloured header row and banded body rows.
const table = (headers, rows, widths, colour = C.accent) => {
  const total = widths.reduce((a, b) => a + b, 0);
  const cols = widths.map((w) => Math.round((w / total) * W));

  const headRow = new d.TableRow({
    tableHeader: true,
    children: headers.map((htext, i) => new d.TableCell({
      width: { size: cols[i], type: d.WidthType.DXA },
      shading: { type: d.ShadingType.CLEAR, fill: colour },
      margins: { top: 90, bottom: 90, left: 120, right: 120 },
      children: [new d.Paragraph({
        children: [t(htext, { size: 17, bold: true, color: 'FFFFFF', characterSpacing: 20 })],
        spacing: { before: 0, after: 0 },
      })],
    })),
  });

  const bodyRows = rows.map((r, ri) => new d.TableRow({
    children: r.map((cellText, i) => new d.TableCell({
      width: { size: cols[i], type: d.WidthType.DXA },
      shading: { type: d.ShadingType.CLEAR, fill: ri % 2 ? 'FFFFFF' : C.band },
      margins: { top: 90, bottom: 90, left: 120, right: 120 },
      children: [new d.Paragraph({
        children: Array.isArray(cellText) ? cellText : [t(String(cellText), { size: 18, color: C.ink2 })],
        spacing: { before: 0, after: 0, line: 260 },
      })],
    })),
  }));

  return new d.Table({
    width: { size: W, type: d.WidthType.DXA },
    columnWidths: cols,
    rows: [headRow, ...bodyRows],
    borders: {
      top:              { style: d.BorderStyle.SINGLE, size: 2, color: C.rule },
      bottom:           { style: d.BorderStyle.SINGLE, size: 2, color: C.rule },
      left:             { style: d.BorderStyle.NIL },
      right:            { style: d.BorderStyle.NIL },
      insideHorizontal: { style: d.BorderStyle.SINGLE, size: 2, color: C.rule },
      insideVertical:   { style: d.BorderStyle.NIL },
    },
  });
};

const numbering = {
  config: [
    {
      reference: 'dots',
      levels: [
        { level: 0, format: d.LevelFormat.BULLET, text: '●', alignment: d.AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 380, hanging: 220 } }, run: { color: C.green, size: 16 } } },
        { level: 1, format: d.LevelFormat.BULLET, text: '○', alignment: d.AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 760, hanging: 220 } }, run: { color: C.blue, size: 16 } } },
      ],
    },
    {
      reference: 'steps',
      levels: [
        { level: 0, format: d.LevelFormat.DECIMAL, text: '%1.', alignment: d.AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 400, hanging: 260 } }, run: { color: C.accent, bold: true } } },
      ],
    },
  ],
};

const docOptions = (title, footerText, colour) => ({
  creator: 'CCredits',
  title,
  description: footerText,
  numbering,
  styles: {
    default: {
      document: { run: { font: 'Calibri', size: 20, color: C.ink2 } },
    },
  },
  sections: [{
    properties: {
      page: { margin: { top: 1180, bottom: 1100, left: 1080, right: 1080 } },
    },
    footers: {
      default: new d.Footer({
        children: [
          new d.Paragraph({
            text: '',
            border: { top: { style: d.BorderStyle.SINGLE, size: 6, color: colour, space: 6 } },
            spacing: { after: 60 },
          }),
          new d.Paragraph({
            tabStops: [{ type: d.TabStopType.RIGHT, position: W }],
            children: [
              t(footerText, { size: 15, color: C.ink3 }),
              new d.TextRun({ children: [new d.Tab()] }),
              t('Pilot data. Not verified.', { size: 15, color: C.goldInk, bold: true }),
            ],
          }),
        ],
      }),
    },
    children: [],
  }],
});

module.exports = { d, C, W, t, P, spacer, titleBlock, h1, h2, h3, bullet, numbered,
                   mono, code, callout, table, numbering, docOptions };
