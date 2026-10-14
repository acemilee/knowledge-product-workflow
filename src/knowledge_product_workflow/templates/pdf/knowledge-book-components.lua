-- knowledge-product-pdf-v4-components-filter
-- knowledge-product-pdf-v4-diagram-layout
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
    element.attributes["height"] = "0.52\\textheight"
    return element
  end
  return nil
end
