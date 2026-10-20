-- knowledge-product-pdf-v6-components-filter
-- knowledge-product-pdf-v6-diagram-layout
local boxes = {
  ["kp-chapter-lead"] = "KpChapterLead",
  ["kp-source"] = "KpSourceCard",
  ["kp-key-takeaway"] = "KpKeyTakeaway",
  ["kp-practice"] = "KpPractice",
  ["kp-note"] = "KpNote",
  ["kp-decision"] = "KpDecision",
  ["kp-procedure"] = "KpProcedure",
  ["kp-worksheet"] = "KpWorksheet",
  ["kp-percentage-distribution"] = "KpPercentageDistribution",
}

local escapes = {
  ["\\"] = "\\textbackslash{}", ["{"] = "\\{", ["}"] = "\\}",
  ["$"] = "\\$", ["&"] = "\\&", ["#"] = "\\#", ["%"] = "\\%",
  ["_"] = "\\_", ["^"] = "\\textasciicircum{}", ["~"] = "\\textasciitilde{}",
}

local function inline_code(text)
  local pieces = {}
  local whole = {}
  for offset, point in utf8.codes(text) do
    local character = utf8.char(point)
    local escaped = escapes[character] or character
    -- Emit a real font-space glyph, so wrapping does not erase data spaces.
    if character == " " then escaped = "\\char32{}" end
    table.insert(whole, escaped)
    local glyph = offset == 1 and "KpCodeFirst" or
                  (offset + #character > #text and "KpCodeLast" or "KpCodeGlyph")
    table.insert(pieces, "\\" .. glyph .. "{" .. escaped .. "}")
    if offset + #character <= #text then
      local natural = character:match("[/%.%-%_: ]")
      if natural then
        table.insert(pieces, "\\allowbreak{}")
      else
        -- Prefer delimiters and surrounding prose spaces; an indivisible
        -- identifier still needs a fallback inside a narrow cell or line.
        -- Penalties paint no glyph and preserve the original payload.
        table.insert(pieces, "\\penalty100{}")
      end
    end
  end
  -- Break opportunities stay within the exact payload; no block in RawInline.
  return "\\KpCodeSmart{" .. table.concat(whole) .. "}{" .. table.concat(pieces) .. "}\\nobreak{}"
end

function Code(element)
  return pandoc.RawInline("latex", inline_code(element.text))
end

local function latin_edge(inline, last)
  if not inline then return false end
  if inline.t == "Str" then
    return inline.text:match(last and "[A-Za-z0-9]$" or "^[A-Za-z0-9]") ~= nil
  end
  if inline.t == "Strong" or inline.t == "Emph" or inline.t == "Span" or inline.t == "Link" then
    return latin_edge(inline.content[last and #inline.content or 1], last)
  end
  return false -- Never alter Code, URL targets or raw markup payloads.
end

local function cjk_edge(inline, last)
  if not inline then return false end
  if inline.t == "Str" then
    local point
    if last then
      for _, value in utf8.codes(inline.text) do point = value end
    else
      point = utf8.codepoint(inline.text, 1)
    end
    return point ~= nil and ((point >= 0x3400 and point <= 0x9FFF) or
      (point >= 0xF900 and point <= 0xFAFF) or (point >= 0x20000 and point <= 0x3134F))
  end
  if inline.t == "Strong" or inline.t == "Emph" or inline.t == "Span" or inline.t == "Link" then
    return cjk_edge(inline.content[last and #inline.content or 1], last)
  end
  return false
end

local function inline_math(inline)
  return inline and inline.t == "Math" and inline.mathtype == "InlineMath"
end

local function math_edges(inlines)
  local result = pandoc.List()
  for index, inline in ipairs(inlines) do
    local previous, following = inlines[index-1], inlines[index+1]
    if inline.t == "Space" and ((cjk_edge(previous, true) and
        (latin_edge(following, false) or inline_math(following))) or
        ((latin_edge(previous, true) or inline_math(previous)) and cjk_edge(following, false))) then
      -- xeCJK owns this boundary. Consuming the external Space makes spaced
      -- and unspaced prose equivalent without touching any protected payload.
    elseif inline_math(inline) then
      if previous and previous.t == "Space" and latin_edge(inlines[index-2], true) then
        result:remove(#result)
        result:insert(pandoc.RawInline("latex", "\\KpMixedGap{}"))
      elseif latin_edge(previous, true) then
        result:insert(pandoc.RawInline("latex", "\\KpMixedGap{}"))
      end
      result:insert(inline)
      if latin_edge(following, false) then
        result:insert(pandoc.RawInline("latex", "\\KpMixedGap{}"))
      end
    elseif inline.t == "Space" and inline_math(previous) and latin_edge(following, false) then
      result:insert(pandoc.RawInline("latex", "\\KpMixedGap{}"))
    else
      result:insert(inline)
    end
  end
  return result
end

local function standalone_code(element)
  local code = element.content[1]
  if not code or code.t ~= "Code" or utf8.len(code.text) <= 56 then return nil end
  if #element.content == 1 then
    return pandoc.RawBlock("latex", "\\begin{KpLongCodeBox}" .. inline_code(code.text) .. "\\end{KpLongCodeBox}")
  end
  return nil
end

local function escaped_text(text)
  return text:gsub("[\\{}$&#%%_^~]", escapes)
end

local function reserve_code(code, context)
  -- Keep a code/context unit that fits one full page; oversized code can split.
  local result = {"\\KpReserveBegin{24pt}"}
  for _, block in ipairs(context or {}) do
    if block.t == "Header" then
      table.insert(result, "\\advance\\KpMeasuredHeight by 64pt")
    else
      table.insert(result, "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(block)) .. "}")
    end
  end
  for line in (code.text .. "\n"):gmatch("([^\n]*)\n") do
    table.insert(result, "\\KpMeasureCodeLine{" .. escaped_text(line:gsub("\t", "    ")) .. "}")
  end
  table.insert(result, "\\KpReserveEnd")
  return pandoc.RawBlock("latex", table.concat(result, "\n"))
end

local function reserve_list(list, context)
  local result = {"\\KpReserveBegin{24pt}", "\\KpReserveBreakable"}
  local function measure(block)
    if block.t == "Header" then
      table.insert(result, "\\advance\\KpMeasuredHeight by 64pt")
    elseif block.t == "CodeBlock" then
      table.insert(result, "\\advance\\KpMeasuredHeight by 24pt")
      for line in (block.text .. "\n"):gmatch("([^\n]*)\n") do
        table.insert(result, "\\KpMeasureCodeLine{" .. escaped_text(line:gsub("\t", "    ")) .. "}")
      end
    elseif block.t == "OrderedList" or block.t == "BulletList" then
      for _, item in ipairs(block.content) do for _, child in ipairs(item) do measure(child) end end
    elseif block.t == "Para" or block.t == "Plain" then
      table.insert(result, "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(block)) .. "}")
    end
  end
  for _, block in ipairs(context or {}) do measure(block) end
  measure(list)
  table.insert(result, "\\KpReserveEnd")
  return pandoc.RawBlock("latex", table.concat(result, "\n"))
end

function CodeBlock(element)
  local result, count = {}, 0
  for _ in (element.text .. "\n"):gmatch("([^\n]*)\n") do count = count + 1 end
  if not element.attributes["kp-reserved"] then table.insert(result, reserve_code(element)) end
  table.insert(result, pandoc.RawBlock("latex", "\\KpCodeTailLines{" .. count .. "}"))
  table.insert(result, element)
  table.insert(result, pandoc.RawBlock("latex", "\\KpCodeTailLines{0}"))
  return result
end

local function display_formula(block)
  return block and block.t == "Para" and #block.content == 1 and
    block.content[1].t == "Math" and block.content[1].mathtype == "DisplayMath"
end

local function reserve_formula(formula, context)
  local result = {"\\KpReserveBegin{24pt}"}
  for _, block in ipairs(context or {}) do
    table.insert(result, block.t == "Header" and "\\advance\\KpMeasuredHeight by 64pt" or
      "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(block)) .. "}")
  end
  local latex = pandoc.write(pandoc.Pandoc({formula}), "latex")
  table.insert(result, "\\KpMeasureReaderBlock{" .. latex .. "}")
  table.insert(result, "\\KpReserveEnd")
  return pandoc.RawBlock("latex", table.concat(result, "\n"))
end

local function reserve_quote(quote, context)
  local result = {"\\KpReserveBegin{24pt}", "\\KpReserveBreakable"}
  for _, block in ipairs(context or {}) do
    table.insert(result, block.t == "Header" and "\\advance\\KpMeasuredHeight by 64pt" or
      "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(block)) .. "}")
  end
  table.insert(result, "\\KpMeasureReaderBlock{" .. pandoc.write(pandoc.Pandoc({quote}), "latex") .. "}")
  table.insert(result, "\\KpReserveEnd")
  return pandoc.RawBlock("latex", table.concat(result, "\n"))
end

local function controlled_figure(block)
  if not block then return nil end
  local image = nil
  if block.t == "Figure" then
    block:walk({Image = function(value) image = value end})
  elseif block.t == "Para" and #block.content == 1 and block.content[1].t == "Image" then
    image = block.content[1]
  end
  if image and image.src:match("^assets/figures/[A-Za-z0-9_%-]+%.pdf$") then return image end
  return nil
end

local function reserve_figure(image, context)
  -- The float owns its actual image/caption geometry. Do not measure a second,
  -- wider copy or keep several paragraphs with the whole image. Protect only
  -- the introduction start; same-section following prose can fill this page.
  local lines = context and context[1] and context[1].t == "Header" and 5 or 3
  return pandoc.RawBlock("latex", "\\Needspace{" .. lines .. "\\baselineskip}")
end

local function reader_blocks(blocks)
  local result = pandoc.List()
  local reserved_lists = {}
  local source_groups = {}
  local reserved_tables = {}
  local reserved_formulas = {}
  local reserved_figures = {}
  local reserved_quotes = {}
  local function closing_operation_tail(position)
    local tail, next_index, has_code = {}, position + 1, false
    while blocks[next_index] and #tail < 4 and
        (blocks[next_index].t == "Para" or blocks[next_index].t == "CodeBlock") do
      table.insert(tail, blocks[next_index])
      has_code = has_code or blocks[next_index].t == "CodeBlock"
      next_index = next_index + 1
    end
    local following = blocks[next_index]
    while following and following.t == "Div" and #following.content == 0 do
      next_index = next_index + 1
      following = blocks[next_index]
    end
    if not has_code or (following and not (following.t == "Header" and following.level == 1)) then return nil end
    for _, item in ipairs(tail) do
      if item.t == "CodeBlock" then item.attributes["kp-reserved"] = "1" end
    end
    return tail
  end
  local function closing_paragraphs(position, limit)
    local tail, next_index = {}, position + 1
    while blocks[next_index] and blocks[next_index].t == "Para" and #tail < limit do
      table.insert(tail, blocks[next_index])
      next_index = next_index + 1
    end
    local following = blocks[next_index]
    -- Quarto appends an empty bibliography Div even when no bibliography is
    -- rendered. It is not reader content or a new teaching unit.
    while following and following.t == "Div" and #following.content == 0 do
      next_index = next_index + 1
      following = blocks[next_index]
    end
    if #tail == 0 or (following and not (following.t == "Header" and following.level == 1)) then return nil end
    return tail
  end
  local function closing_table_measurements(position)
    local tail = closing_paragraphs(position, 2)
    local closes_with_text = tail ~= nil
    tail = tail or {}
    local table_block = blocks[position]
    local rows, measured = {}, {}
    for _, row in ipairs(table_block.head.rows) do table.insert(rows, row) end
    for _, body in ipairs(table_block.bodies) do
      for _, row in ipairs(body.head) do table.insert(rows, row) end
      for _, row in ipairs(body.body) do table.insert(rows, row) end
    end
    for _, row in ipairs(table_block.foot.rows) do table.insert(rows, row) end
    for _, row in ipairs(rows) do
      table.insert(measured, "\\KpTableRowBegin")
      for column, cell in ipairs(row.cells) do
        if cell.row_span ~= 1 or cell.col_span ~= 1 then return nil end
        for _, block in ipairs(cell.contents) do
          if block.t ~= "Plain" and block.t ~= "Para" then return nil end
        end
        local width = table_block.colspecs[column][2]
        if not width or width == 0 then width = 1 / #table_block.colspecs end
        local text = pandoc.write(pandoc.Pandoc(cell.contents):walk({Code = Code, Inlines = math_edges}), "latex")
        table.insert(measured, "\\KpMeasureTableCell{" .. width .. "}{" ..
          (2 * (#table_block.colspecs - 1)) .. "}{" .. text .. "}")
      end
      table.insert(measured, "\\advance\\KpMeasuredHeight by \\KpMeasuredRowHeight")
    end
    for _, paragraph in ipairs(tail) do
      table.insert(measured, "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(paragraph)) .. "}")
    end
    return measured, closes_with_text
  end
  local function reserve_table_intro(context, position)
    local reserve = {"\\KpReserveBegin{24pt}", "\\advance\\KpMeasuredHeight by 4\\baselineskip"}
    local closing, closes_with_text = closing_table_measurements(position)
    if closing then
      reserve = {"\\KpReserveBegin{36pt}"}
      for _, command in ipairs(closing) do table.insert(reserve, command) end
    end
    for _, item in ipairs(context) do
      table.insert(reserve, item.t == "Header" and "\\advance\\KpMeasuredHeight by 64pt" or
        "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(item)) .. "}")
    end
    -- Only a small closing table may be kept together. A medium table that
    -- fits a whole page must still continue rather than push half a page out.
    table.insert(reserve, "\\KpReserveLimit=.25\\textheight")
    table.insert(reserve, "\\KpReserveEnd")
    return pandoc.RawBlock("latex", table.concat(reserve, "\n"))
  end
  -- Keep a finite closing explanation with its source, instead of reserving
  -- only the last small paragraph and sending it to an otherwise empty page.
  for index, block in ipairs(blocks) do
    local final_summary = false
    if block.t == "Div" and block.classes:includes("kp-key-takeaway") then
      final_summary = true
      for following = index + 1, #blocks do
        if blocks[following].t == "Header" and blocks[following].level == 1 then break end
        if blocks[following].t ~= "Div" or #blocks[following].content > 0 then final_summary = false end
      end
    end
    local final_paragraph = block.t == "Para" and closing_paragraphs(index - 1, 1) ~= nil
    if (block.t == "Div" and (block.classes:includes("kp-source") or final_summary)) or final_paragraph then
      local first = index
      while first > 1 and index - first < 3 and blocks[first-1].t == "Para" do
        first = first - 1
      end
      if first < index or final_paragraph then
        if first > 1 and blocks[first-1].t == "Header" then first = first - 1 end
        source_groups[first] = index
      end
    end
  end
  for index, block in ipairs(blocks) do
    -- A standalone bold label is a paragraph, not a Header. Keep its next
    -- explanatory paragraph with it using the same finite measured reserve.
    if block.t == "Para" and #block.content == 1 and block.content[1].t == "Strong" and
        blocks[index+1] and blocks[index+1].t == "Para" then
      source_groups[index] = index + 1
    end
    if source_groups[index] then
      local reserve = {"\\KpReserveBegin{24pt}"}
      for position = index, source_groups[index] do
        local context = blocks[position]
        if context.t == "Header" then
          table.insert(reserve, "\\advance\\KpMeasuredHeight by 64pt")
        else
          table.insert(reserve, "\\KpMeasureReaderText{" .. escaped_text(pandoc.utils.stringify(context)) .. "}")
        end
      end
      table.insert(reserve, "\\KpReserveEnd")
      result:insert(pandoc.RawBlock("latex", table.concat(reserve, "\n")))
    end
    if block.t == "Header" then
      local context = {block}
      local next_index = index + 1
      while blocks[next_index] and blocks[next_index].t == "Para" and not display_formula(blocks[next_index]) and
          not controlled_figure(blocks[next_index]) and #context < 4 do
        table.insert(context, blocks[next_index])
        next_index = next_index + 1
      end
      local code = blocks[next_index]
      if code and code.t == "CodeBlock" then
        code.attributes["kp-reserved"] = "1"
        result:insert(reserve_code(code, context))
      elseif code and (code.t == "OrderedList" or code.t == "BulletList") then
        reserved_lists[next_index] = true
        for _, item in ipairs(closing_operation_tail(next_index) or {}) do table.insert(context, item) end
        result:insert(reserve_list(code, context))
      elseif code and code.t == "Table" then
        reserved_tables[next_index] = true
        result:insert(reserve_table_intro(context, next_index))
      elseif display_formula(code) then
        reserved_formulas[next_index] = true
        result:insert(reserve_formula(code, context))
      elseif controlled_figure(code) then
        reserved_figures[next_index] = true
        result:insert(reserve_figure(controlled_figure(code), context))
      elseif code and code.t == "BlockQuote" then
        reserved_quotes[next_index] = true
        result:insert(reserve_quote(code, context))
      end
    elseif block.t == "Para" and blocks[index+1] and blocks[index+1].t == "BlockQuote" and not reserved_quotes[index+1] then
      reserved_quotes[index+1] = true
      result:insert(reserve_quote(blocks[index+1], {block}))
    elseif block.t == "Para" and controlled_figure(blocks[index+1]) and not reserved_figures[index+1] then
      reserved_figures[index+1] = true
      result:insert(reserve_figure(controlled_figure(blocks[index+1]), {block}))
    elseif block.t == "Para" and not display_formula(block) and display_formula(blocks[index+1]) and
        not reserved_formulas[index+1] then
      reserved_formulas[index+1] = true
      result:insert(reserve_formula(blocks[index+1], {block}))
    elseif block.t == "Para" and blocks[index+1] and blocks[index+1].t == "CodeBlock" and
        blocks[index+1].attributes["kp-reserved"] ~= "1" then
      -- A run instruction introduces the following command, not the earlier
      -- large example. Reserve before the paragraph so it cannot be orphaned.
      blocks[index+1].attributes["kp-reserved"] = "1"
      result:insert(reserve_code(blocks[index+1], {block}))
    elseif block.t == "Para" and blocks[index+1] and blocks[index+1].t == "Table" and not reserved_tables[index+1] then
      result:insert(reserve_table_intro({block}, index+1))
    elseif block.t == "Para" and blocks[index+1] and
        (blocks[index+1].t == "OrderedList" or blocks[index+1].t == "BulletList") and not reserved_lists[index+1] then
      local tail = closing_operation_tail(index+1) or closing_paragraphs(index+1, 3)
      local context = {block}
      for _, paragraph in ipairs(tail or {}) do table.insert(context, paragraph) end
      result:insert(reserve_list(blocks[index+1], context))
      reserved_lists[index+1] = true
    elseif (block.t == "OrderedList" or block.t == "BulletList") and not reserved_lists[index] then
      result:insert(reserve_list(block))
    end
    result:insert(block)
  end
  return result
end

function Header(element)
  local first = element.content[1]
  if first and first.t == "Str" and first.text:match("^%d+[%.%d]*%.?$") then
    local number = first.text
    element.content[1] = pandoc.RawInline("latex", "\\texorpdfstring{\\textcolor{kpBrand}{" .. number .. "}\\KpMixedGap{}}{" .. number .. " }")
    if element.content[2] and element.content[2].t == "Space" then element.content:remove(2) end
    return element
  end
  return nil
end

-- Whole-row brand tint is set by rowcolors in the publication style.
-- A cell-level rowcolor is invalid inside Pandoc's minipage column headers.

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
      element.attributes["width"] = "100%"
    end
    if element.attributes["height"] == nil then
      element.attributes["height"] = "145mm"
    end
    return element
  end
  return nil
end

function Figure(element)
  if controlled_figure(element) then
    element.attributes["fig-pos"] = "!htbp"
    element:walk({Image = function(image) image.attributes["fig-pos"] = "!htbp"; return image end})
    return element
  end
end

return {{Blocks = reader_blocks, Para = standalone_code}, {Inlines = math_edges, Code = Code, CodeBlock = CodeBlock, Header = Header, Div = Div, Image = Image, Figure = Figure}}
