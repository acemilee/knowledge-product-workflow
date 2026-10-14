-- knowledge-product-pdf-v6-components-filter
-- knowledge-product-pdf-v6-diagram-layout
local boxes = {
  ["kp-source"] = "KpSourceCard",
  ["kp-key-takeaway"] = "KpKeyTakeaway",
  ["kp-practice"] = "KpPractice",
  ["kp-note"] = "KpNote",
  ["kp-decision"] = "KpDecision",
  ["kp-procedure"] = "KpProcedure",
  ["kp-worksheet"] = "KpWorksheet",
  ["kp-percentage-distribution"] = "KpPercentageDistribution",
}

function Header(element)
  local first = element.content[1]
  if first and first.t == "Str" and first.text:match("^%d+[%.%d]*%.?$") then
    local number = first.text
    element.content[1] = pandoc.RawInline("latex", "\\texorpdfstring{\\textcolor{kpBrand}{" .. number .. "}}{" .. number .. "}")
    return element
  end
  return nil
end

function Table(element)
  if element.head and element.head.rows then
    for _, row in ipairs(element.head.rows) do
      if row.cells[1] then
        table.insert(row.cells[1].contents, 1, pandoc.RawBlock("latex", "\\rowcolor{kpBrandSoft}"))
      end
    end
    return element
  end
  return nil
end

function Div(element)
  for class_name, environment in pairs(boxes) do
    if element.classes:includes(class_name) then
      table.insert(element.content, 1, pandoc.RawBlock("latex", "\\begin{" .. environment .. "}"))
      table.insert(element.content, pandoc.RawBlock("latex", "\\end{" .. environment .. "}"))
      return element.content
    end
  end
  return nil
end

function Image(element)
  -- D2 assets are reader diagrams, not decorative full-page artwork. Bound
  -- their PDF height while keeping canonical Markdown unchanged.
  if element.src:match("^assets/figures/.*%.pdf$") then
    if element.attributes["width"] == nil then
      element.attributes["width"] = "0.72\\textwidth"
    end
    return element
  end
  return nil
end
