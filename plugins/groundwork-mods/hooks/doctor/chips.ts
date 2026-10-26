// Chips: small icon buttons on a faint tile. While the pointer rests on
// an icon its label shows at the right end of the row, laid over it, so
// nothing moves and no room is kept for it. On a
// surface without a pointer (the phone) the label stays hidden and the icon
// still works.

import type { BoxProps, ButtonProps, RenderElement, RenderNode, TextProps } from 'claude-code'

export type Els = {
  Box: (p: BoxProps & { children?: RenderNode[] }) => RenderElement
  Text: (p: TextProps & { children?: RenderNode[] }) => RenderElement
  Button: (p: ButtonProps & { children?: RenderNode[] }) => RenderElement
}

export type Chip = {
  key: string
  glyph: string
  tip: string
  on?: boolean
  color?: string
  onPress: () => unknown
}

const tile = (light: boolean) => (light ? '#E9EEF4' : '#16263D')
const tileHover = (light: boolean) => (light ? '#D3DEEA' : '#24406A')

// One icon button: the glyph in the accent when on, dim when off. The keyed
// Box around it is what the pointer heats; a Text inside a Button cannot.
export function chip(els: Els, c: Chip, light: boolean): RenderElement {
  const scope = 'tip-' + c.key
  return els.Box({
    key: 'chip-' + c.key,
    hover: { scope },
    children: [
      els.Button({
        key: c.key,
        plain: true,
        hover: { scope, bold: true },
        onPress: () => void c.onPress(),
        children: [
          els.Text({
            backgroundColor: tile(light),
            color: c.on === false ? 'inactive' : c.color ?? (light ? '#0B6B5B' : '#3EDCB0'),
            hover: { scope, backgroundColor: tileHover(light), bold: true },
            children: [' ' + c.glyph + ' '],
          }),
        ],
      }),
    ],
  })
}

// The labels: one box laid over the row, out of the flow, so showing a label
// moves nothing and takes no room. One line, cut at the edge.
//   'right': at the row's right end. Draw it as the row's LAST child, so it
//            paints over whatever the row holds there (tips()).
//   'left':  left of the chips, over what comes before them: for a chip at
//            the right end of a section line (chipRow(..., 'left')).
export function hints(els: Els, chips: Chip[], side: 'right' | 'left', left = 0, top = 0, right = 0): RenderElement {
  const width = Math.max(...chips.map(c => c.tip.length)) + 2
  return els.Box({
    position: 'absolute',
    top,
    ...(side === 'right' ? { right } : { left: left - width }),
    width,
    children: chips.map(c =>
      els.Box({
        key: 'hint-' + c.key,
        display: 'none',
        hover: { scope: 'tip-' + c.key, display: 'flex' },
        flexDirection: 'row',
        justifyContent: 'flex-end',
        width,
        children: [els.Text({ dimColor: true, italic: true, wrap: 'truncate-end', children: [' ' + c.tip + ' '] })],
      }),
    ),
  })
}

// The labels of a row of chips, right-aligned on that row. Put it last in
// the row Box that spans the width. In a band, 'rule' puts the label on the
// band's top line instead, so a full row stays readable while you hover.
export function tips(els: Els, chips: Chip[], where: 'row' | 'rule' = 'row'): RenderElement {
  return where === 'rule' ? hints(els, chips, 'right', 0, -1, 1) : hints(els, chips, 'right')
}

// A row of chips. With 'left' it carries its own labels, shown left of the
// chips; otherwise the row it sits in draws them with tips().
export function chipRow(els: Els, chips: Chip[], light: boolean, side?: 'left'): RenderElement {
  return els.Box({
    flexDirection: 'row',
    columnGap: 1,
    alignItems: 'flex-start',
    children: [...chips.map(c => chip(els, c, light)), ...(side === 'left' ? [hints(els, chips, 'left')] : [])],
  })
}

// A section title: bold in the theme's blue, a thin line to the edge, and
// whatever sits at its right end (a chip).
export function section(els: Els, title: string, cols: number, extra?: RenderElement): RenderElement {
  const rule = Math.max(3, cols - title.length - (extra ? 6 : 1))
  return els.Box({
    flexDirection: 'row',
    columnGap: 1,
    children: [
      els.Text({ bold: true, color: 'suggestion', wrap: 'truncate-end', children: [title] }),
      els.Text({ color: 'suggestion', wrap: 'truncate-end', children: ['─'.repeat(rule)] }),
      ...(extra ? [extra] : []),
    ],
  })
}
