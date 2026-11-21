-- Pandoc treats a document's "Title" (e.g. Word's Title style) as metadata,
-- which disappears in most output formats. Turn it back into a visible heading.
function Pandoc(doc)
  local title = doc.meta.title
  if title == nil then
    return doc
  end
  local inlines = pandoc.utils.type(title) == "Inlines" and title or pandoc.Inlines(pandoc.utils.stringify(title))
  local blocks = { pandoc.Header(1, inlines) }
  local subtitle = doc.meta.subtitle
  if subtitle ~= nil then
    table.insert(blocks, pandoc.Para({ pandoc.Emph(pandoc.Inlines(pandoc.utils.stringify(subtitle))) }))
  end
  for _, b in ipairs(doc.blocks) do
    table.insert(blocks, b)
  end
  doc.blocks = blocks
  doc.meta.title = nil
  doc.meta.subtitle = nil
  return doc
end
