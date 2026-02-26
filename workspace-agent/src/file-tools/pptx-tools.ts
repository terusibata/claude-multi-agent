/**
 * PowerPoint file tools
 *
 * AI agent tools for understanding PowerPoint files:
 * 1. get_presentation_info: Get presentation structure and basic info
 * 2. get_slides_content: Get content from specified slides (range)
 * 3. search_presentation: Search keywords across the presentation
 *
 * Uses jszip + fast-xml-parser to parse .pptx (Office Open XML) files.
 */

import JSZip from "jszip";
import { XMLParser } from "fast-xml-parser";
import { createLogger } from "../logger.js";
import {
  buildSearchPattern,
  createContextSnippet,
  localFileToolHandler,
  formatToolError,
  formatToolSuccess,
  normalizeText,
  type ToolResult,
} from "./utils.js";

const logger = createLogger("pptx-tools");

// =============================================================================
// Types
// =============================================================================

interface SlideInfo {
  number: number;
  title: string;
  textCount: number;
  imageCount: number;
  tableCount: number;
  chartCount: number;
  charCount: number;
  hasNotes: boolean;
  notesLength: number;
}

interface PresentationInfo {
  filename: string;
  totalSlides: number;
  totalCharacters: number;
  slides: SlideInfo[];
}

interface SlideContent {
  number: number;
  title: string;
  textContent: string[];
  tableContent: string[];
  notes: string | null;
}

interface SlidesResult {
  filename: string;
  requestedSlides: string;
  totalSlides: number;
  returnedSlides: number;
  startSlide: number;
  endSlide: number;
  hasMore: boolean;
  slides: SlideContent[];
}

interface PptxSearchHit {
  slideNumber: number;
  slideTitle: string;
  locationType: "text" | "table" | "notes" | "title";
  text: string;
  context: string;
}

interface PptxSearchResult {
  query: string;
  totalHits: number;
  hits: PptxSearchHit[];
}

// =============================================================================
// Constants
// =============================================================================

const DEFAULT_MAX_SLIDES = 10;

// =============================================================================
// Internal Utilities - OOXML Parsing
// =============================================================================

const xmlParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  removeNSPrefix: true,
  isArray: (name: string) => {
    // Ensure certain elements are always arrays for consistent parsing
    const arrayElements = [
      "sp",
      "pic",
      "graphicFrame",
      "grpSp",
      "cxnSp",
      "tbl",
      "tr",
      "tc",
      "p",
      "r",
      "t",
      "sldId",
      "spTree",
    ];
    return arrayElements.includes(name);
  },
});

/**
 * Recursively extract all text from an XML node.
 * Follows the OOXML text body structure: txBody -> p -> r -> t
 */
function extractTextFromNode(node: unknown): string[] {
  const texts: string[] = [];

  if (node === null || node === undefined) return texts;
  if (typeof node === "string") return [node];

  if (Array.isArray(node)) {
    for (const item of node) {
      texts.push(...extractTextFromNode(item));
    }
    return texts;
  }

  if (typeof node !== "object") return [];

  const obj = node as Record<string, unknown>;

  // If this is a text node 't', extract its value
  if ("t" in obj) {
    const tVal = obj.t;
    if (Array.isArray(tVal)) {
      for (const t of tVal) {
        if (typeof t === "string") {
          texts.push(t);
        } else if (t && typeof t === "object" && "#text" in (t as Record<string, unknown>)) {
          texts.push(String((t as Record<string, unknown>)["#text"]));
        }
      }
    } else if (typeof tVal === "string") {
      texts.push(tVal);
    } else if (tVal && typeof tVal === "object" && "#text" in (tVal as Record<string, unknown>)) {
      texts.push(String((tVal as Record<string, unknown>)["#text"]));
    }
  }

  // Recurse into child elements
  for (const key of Object.keys(obj)) {
    if (key.startsWith("@_") || key === "#text") continue;
    texts.push(...extractTextFromNode(obj[key]));
  }

  return texts;
}

/**
 * Extract text from paragraphs (a:p elements) in a text body.
 * Returns one string per paragraph.
 */
function extractParagraphTexts(txBody: unknown): string[] {
  if (!txBody || typeof txBody !== "object") return [];

  const body = txBody as Record<string, unknown>;
  const pElements = body.p;
  if (!pElements) return [];

  const paragraphs = Array.isArray(pElements) ? pElements : [pElements];
  const results: string[] = [];

  for (const p of paragraphs) {
    if (!p || typeof p !== "object") continue;
    const pObj = p as Record<string, unknown>;

    // Collect text from 'r' (run) elements
    const runs = pObj.r;
    if (!runs) continue;

    const runList = Array.isArray(runs) ? runs : [runs];
    const lineTexts: string[] = [];

    for (const run of runList) {
      if (!run || typeof run !== "object") continue;
      const runObj = run as Record<string, unknown>;
      const tVal = runObj.t;

      if (typeof tVal === "string") {
        lineTexts.push(tVal);
      } else if (Array.isArray(tVal)) {
        for (const t of tVal) {
          if (typeof t === "string") {
            lineTexts.push(t);
          } else if (t && typeof t === "object" && "#text" in (t as Record<string, unknown>)) {
            lineTexts.push(String((t as Record<string, unknown>)["#text"]));
          }
        }
      } else if (tVal && typeof tVal === "object" && "#text" in (tVal as Record<string, unknown>)) {
        lineTexts.push(String((tVal as Record<string, unknown>)["#text"]));
      }
    }

    const text = lineTexts.join("");
    if (text.trim()) {
      results.push(text);
    }
  }

  return results;
}

/**
 * Parse a PPTX slide XML to extract shapes, text, tables, images, charts.
 */
interface ParsedSlide {
  title: string;
  textParagraphs: string[];
  tableRows: string[];
  imageCount: number;
  tableCount: number;
  chartCount: number;
  textCount: number;
}

function parseSlideXml(xml: string): ParsedSlide {
  const parsed = xmlParser.parse(xml);
  const sld = parsed.sld || parsed;
  const cSld = sld?.cSld;
  if (!cSld) {
    return {
      title: "(タイトルなし)",
      textParagraphs: [],
      tableRows: [],
      imageCount: 0,
      tableCount: 0,
      chartCount: 0,
      textCount: 0,
    };
  }

  const spTree = cSld.spTree;
  if (!spTree) {
    return {
      title: "(タイトルなし)",
      textParagraphs: [],
      tableRows: [],
      imageCount: 0,
      tableCount: 0,
      chartCount: 0,
      textCount: 0,
    };
  }

  let title = "(タイトルなし)";
  const textParagraphs: string[] = [];
  const tableRows: string[] = [];
  let imageCount = 0;
  let tableCount = 0;
  let chartCount = 0;
  let textCount = 0;

  // Process shapes (sp)
  const shapes = spTree.sp || [];
  const shapeList = Array.isArray(shapes) ? shapes : [shapes];

  for (const shape of shapeList) {
    if (!shape || typeof shape !== "object") continue;
    const sp = shape as Record<string, unknown>;

    // Check if this is a title placeholder
    const nvSpPr = sp.nvSpPr as Record<string, unknown> | undefined;
    const nvPr = nvSpPr?.nvPr as Record<string, unknown> | undefined;
    const ph = nvPr?.ph as Record<string, unknown> | undefined;
    const phType = ph?.["@_type"];

    const txBody = sp.txBody;
    if (txBody) {
      textCount++;
      const paragraphs = extractParagraphTexts(txBody);

      if (phType === "title" || phType === "ctrTitle") {
        // This is a title shape
        const titleText = paragraphs.join(" ").trim();
        if (titleText) {
          title = normalizeText(titleText).slice(0, 100);
        }
      }

      for (const p of paragraphs) {
        const normalized = normalizeText(p).trim();
        if (normalized) {
          textParagraphs.push(normalized);
        }
      }
    }
  }

  // Process pictures (pic)
  const pics = spTree.pic || [];
  const picList = Array.isArray(pics) ? pics : [pics];
  imageCount = picList.filter((p) => p !== null && p !== undefined).length;

  // Process graphic frames (graphicFrame) - tables and charts
  const graphicFrames = spTree.graphicFrame || [];
  const frameList = Array.isArray(graphicFrames)
    ? graphicFrames
    : [graphicFrames];

  for (const frame of frameList) {
    if (!frame || typeof frame !== "object") continue;
    const gf = frame as Record<string, unknown>;

    // Check for tables
    const graphic = gf.graphic as Record<string, unknown> | undefined;
    const graphicData = graphic?.graphicData as
      | Record<string, unknown>
      | undefined;
    const tbl = graphicData?.tbl as Record<string, unknown> | undefined;

    if (tbl) {
      tableCount++;
      // Extract table rows
      const trElements = tbl.tr;
      if (trElements) {
        const rows = Array.isArray(trElements) ? trElements : [trElements];
        for (const row of rows) {
          if (!row || typeof row !== "object") continue;
          const tcElements = (row as Record<string, unknown>).tc;
          if (!tcElements) continue;

          const cells = Array.isArray(tcElements) ? tcElements : [tcElements];
          const cellTexts: string[] = [];

          for (const cell of cells) {
            if (!cell || typeof cell !== "object") continue;
            const cellObj = cell as Record<string, unknown>;
            const cellTxBody = cellObj.txBody;
            if (cellTxBody) {
              const paragraphs = extractParagraphTexts(cellTxBody);
              cellTexts.push(
                normalizeText(paragraphs.join(" ")).trim(),
              );
            } else {
              cellTexts.push("");
            }
          }

          if (cellTexts.some((t) => t)) {
            tableRows.push("| " + cellTexts.join(" | ") + " |");
          }
        }
      }
    }

    // Check for charts
    if (
      graphicData &&
      (graphicData.chart || graphicData["@_uri"]?.toString().includes("chart"))
    ) {
      chartCount++;
    }
  }

  // If no title found from placeholder, use first text as fallback
  if (title === "(タイトルなし)" && textParagraphs.length > 0) {
    title = textParagraphs[0].slice(0, 50);
  }

  return {
    title,
    textParagraphs,
    tableRows,
    imageCount,
    tableCount,
    chartCount,
    textCount,
  };
}

/**
 * Parse notes XML to extract notes text.
 */
function parseNotesXml(xml: string): string {
  const parsed = xmlParser.parse(xml);
  const notes = parsed.notes || parsed;
  const cSld = notes?.cSld;
  if (!cSld) return "";

  const spTree = cSld.spTree;
  if (!spTree) return "";

  const shapes = spTree.sp || [];
  const shapeList = Array.isArray(shapes) ? shapes : [shapes];

  const allTexts: string[] = [];

  for (const shape of shapeList) {
    if (!shape || typeof shape !== "object") continue;
    const sp = shape as Record<string, unknown>;

    // Check if this is the notes body placeholder
    const nvSpPr = sp.nvSpPr as Record<string, unknown> | undefined;
    const nvPr = nvSpPr?.nvPr as Record<string, unknown> | undefined;
    const ph = nvPr?.ph as Record<string, unknown> | undefined;
    const phType = ph?.["@_type"];

    if (phType === "body") {
      const txBody = sp.txBody;
      if (txBody) {
        const paragraphs = extractParagraphTexts(txBody);
        allTexts.push(...paragraphs);
      }
    }
  }

  return normalizeText(allTexts.join("\n")).trim();
}

/**
 * Parse slide specification string.
 */
function parseSlides(
  slidesSpec: string,
  totalSlides: number,
  maxSlides: number,
): number[] {
  const slideNumbers: number[] = [];
  const parts = slidesSpec.replace(/\s/g, "").split(",");

  for (const part of parts) {
    if (part.includes("-")) {
      try {
        const [startStr, endStr] = part.split("-");
        const startNum = parseInt(startStr, 10);
        const endNum = parseInt(endStr, 10);
        if (isNaN(startNum) || isNaN(endNum)) continue;
        const clampedEnd = Math.min(endNum, totalSlides);
        for (let i = startNum; i <= clampedEnd; i++) {
          slideNumbers.push(i);
        }
      } catch {
        continue;
      }
    } else {
      const num = parseInt(part, 10);
      if (!isNaN(num)) {
        slideNumbers.push(num);
      }
    }
  }

  // Sort, deduplicate, and limit
  const result = [...new Set(slideNumbers)].sort((a, b) => a - b);
  return result.slice(0, maxSlides);
}

/**
 * Get ordered slide file paths from presentation.xml.
 */
async function getSlideOrder(zip: JSZip): Promise<string[]> {
  // Parse presentation.xml to get slide ordering
  const presentationXml = await zip
    .file("ppt/presentation.xml")
    ?.async("string");
  if (!presentationXml) return [];

  const parsed = xmlParser.parse(presentationXml);
  const presentation = parsed.presentation || parsed;
  const sldIdLst = presentation?.sldIdLst;
  if (!sldIdLst) return [];

  const sldIds = sldIdLst.sldId;
  if (!sldIds) return [];

  const idList = Array.isArray(sldIds) ? sldIds : [sldIds];

  // Parse relationships to map rId to file paths
  const relsXml = await zip
    .file("ppt/_rels/presentation.xml.rels")
    ?.async("string");
  if (!relsXml) return [];

  const relsDoc = xmlParser.parse(relsXml);
  const relationships = relsDoc.Relationships?.Relationship;
  if (!relationships) return [];

  const relsList = Array.isArray(relationships)
    ? relationships
    : [relationships];
  const relMap = new Map<string, string>();

  for (const rel of relsList) {
    if (rel?.["@_Id"] && rel?.["@_Target"]) {
      relMap.set(rel["@_Id"], rel["@_Target"]);
    }
  }

  // Map slide IDs to file paths in order
  const slidePaths: string[] = [];
  for (const sldId of idList) {
    const rId = sldId?.["@_r:id"] || sldId?.["@_id"];
    if (rId) {
      const target = relMap.get(rId);
      if (target) {
        // Target is relative to ppt/
        const path = target.startsWith("/")
          ? target.slice(1)
          : `ppt/${target}`;
        slidePaths.push(path);
      }
    }
  }

  return slidePaths;
}

/**
 * Get notes file path for a slide.
 */
async function getNotesPath(
  zip: JSZip,
  slidePath: string,
): Promise<string | null> {
  // Extract slide number from path to find relationships
  const slideFileName = slidePath.split("/").pop();
  if (!slideFileName) return null;

  const relsPath = `ppt/slides/_rels/${slideFileName}.rels`;
  const relsXml = await zip.file(relsPath)?.async("string");
  if (!relsXml) return null;

  const relsDoc = xmlParser.parse(relsXml);
  const relationships = relsDoc.Relationships?.Relationship;
  if (!relationships) return null;

  const relsList = Array.isArray(relationships)
    ? relationships
    : [relationships];

  for (const rel of relsList) {
    const target = rel?.["@_Target"];
    const type = rel?.["@_Type"];
    if (
      type &&
      type.includes("notesSlide") &&
      target
    ) {
      // Target is relative to ppt/slides/
      if (target.startsWith("../")) {
        return `ppt/${target.slice(3)}`;
      }
      return target.startsWith("/") ? target.slice(1) : `ppt/slides/${target}`;
    }
  }

  return null;
}

// =============================================================================
// Core Functions
// =============================================================================

/**
 * Get presentation structure info.
 */
async function getPresentationInfo(
  content: Buffer,
  filename: string,
): Promise<PresentationInfo> {
  const zip = await JSZip.loadAsync(content);
  const slidePaths = await getSlideOrder(zip);

  const slides: SlideInfo[] = [];
  let totalCharacters = 0;

  for (let i = 0; i < slidePaths.length; i++) {
    const slidePath = slidePaths[i];
    const slideXml = await zip.file(slidePath)?.async("string");
    if (!slideXml) continue;

    const parsed = parseSlideXml(slideXml);
    let charCount = 0;

    for (const text of parsed.textParagraphs) {
      charCount += text.length;
    }

    // Notes
    let hasNotes = false;
    let notesLength = 0;
    const notesPath = await getNotesPath(zip, slidePath);
    if (notesPath) {
      const notesXml = await zip.file(notesPath)?.async("string");
      if (notesXml) {
        const notesText = parseNotesXml(notesXml);
        if (notesText) {
          hasNotes = true;
          notesLength = notesText.length;
          charCount += notesLength;
        }
      }
    }

    totalCharacters += charCount;

    slides.push({
      number: i + 1,
      title: parsed.title,
      textCount: parsed.textCount,
      imageCount: parsed.imageCount,
      tableCount: parsed.tableCount,
      chartCount: parsed.chartCount,
      charCount,
      hasNotes,
      notesLength,
    });
  }

  return {
    filename,
    totalSlides: slidePaths.length,
    totalCharacters,
    slides,
  };
}

/**
 * Get content from specified slides.
 */
async function getSlidesContent(
  content: Buffer,
  options: {
    slidesSpec?: string;
    maxSlides?: number;
    includeNotes?: boolean;
    includeTables?: boolean;
  } = {},
): Promise<SlidesResult> {
  const {
    slidesSpec = "1-10",
    maxSlides = DEFAULT_MAX_SLIDES,
    includeNotes = true,
    includeTables = true,
  } = options;

  const zip = await JSZip.loadAsync(content);
  const slidePaths = await getSlideOrder(zip);
  const totalSlides = slidePaths.length;

  const slideNumbers = parseSlides(slidesSpec, totalSlides, maxSlides);

  const slides: SlideContent[] = [];

  for (const slideNum of slideNumbers) {
    if (slideNum < 1 || slideNum > totalSlides) continue;

    const slidePath = slidePaths[slideNum - 1];
    const slideXml = await zip.file(slidePath)?.async("string");
    if (!slideXml) continue;

    const parsed = parseSlideXml(slideXml);

    // Notes
    let notes: string | null = null;
    if (includeNotes) {
      const notesPath = await getNotesPath(zip, slidePath);
      if (notesPath) {
        const notesXml = await zip.file(notesPath)?.async("string");
        if (notesXml) {
          const notesText = parseNotesXml(notesXml);
          if (notesText) {
            notes = notesText;
          }
        }
      }
    }

    slides.push({
      number: slideNum,
      title: parsed.title,
      textContent: parsed.textParagraphs,
      tableContent: includeTables ? parsed.tableRows : [],
      notes,
    });
  }

  // Range info
  let startSlide = 0;
  let endSlide = 0;
  if (slideNumbers.length > 0) {
    startSlide = Math.min(...slideNumbers);
    endSlide = Math.max(...slideNumbers);
  }

  const hasMore = endSlide < totalSlides;

  return {
    filename: "",
    requestedSlides: slidesSpec,
    totalSlides,
    returnedSlides: slides.length,
    startSlide,
    endSlide,
    hasMore,
    slides,
  };
}

/**
 * Search the presentation for a keyword.
 */
async function searchPresentation(
  content: Buffer,
  query: string,
  options: {
    caseSensitive?: boolean;
    maxHits?: number;
    includeNotes?: boolean;
  } = {},
): Promise<PptxSearchResult> {
  const {
    caseSensitive = false,
    maxHits = 50,
    includeNotes = true,
  } = options;

  const zip = await JSZip.loadAsync(content);
  const slidePaths = await getSlideOrder(zip);

  const hits: PptxSearchHit[] = [];
  const pattern = buildSearchPattern(query, caseSensitive);

  for (let slideIdx = 0; slideIdx < slidePaths.length; slideIdx++) {
    if (hits.length >= maxHits) break;

    const slideNum = slideIdx + 1;
    const slidePath = slidePaths[slideIdx];
    const slideXml = await zip.file(slidePath)?.async("string");
    if (!slideXml) continue;

    const parsed = parseSlideXml(slideXml);
    const title = parsed.title;

    // Search title
    pattern.lastIndex = 0;
    let match = pattern.exec(title);
    if (match && hits.length < maxHits) {
      hits.push({
        slideNumber: slideNum,
        slideTitle: title,
        locationType: "title",
        text: title,
        context: createContextSnippet(title, match.index, match.index + match[0].length),
      });
    }

    // Search text paragraphs
    for (const text of parsed.textParagraphs) {
      if (hits.length >= maxHits) break;

      pattern.lastIndex = 0;
      match = pattern.exec(text);
      if (match) {
        hits.push({
          slideNumber: slideNum,
          slideTitle: title,
          locationType: "text",
          text: text.length > 200 ? text.slice(0, 200) : text,
          context: createContextSnippet(text, match.index, match.index + match[0].length),
        });
      }
    }

    // Search table rows
    for (let rowIdx = 0; rowIdx < parsed.tableRows.length; rowIdx++) {
      if (hits.length >= maxHits) break;

      const rowText = parsed.tableRows[rowIdx];
      pattern.lastIndex = 0;
      match = pattern.exec(rowText);
      if (match) {
        hits.push({
          slideNumber: slideNum,
          slideTitle: title,
          locationType: "table",
          text: rowText.length > 200 ? rowText.slice(0, 200) : rowText,
          context: `行${rowIdx + 1}: ${rowText.slice(0, 100)}`,
        });
      }
    }

    // Search notes
    if (includeNotes && hits.length < maxHits) {
      const notesPath = await getNotesPath(zip, slidePath);
      if (notesPath) {
        const notesXml = await zip.file(notesPath)?.async("string");
        if (notesXml) {
          const notesText = parseNotesXml(notesXml);
          if (notesText) {
            pattern.lastIndex = 0;
            match = pattern.exec(notesText);
            if (match) {
              hits.push({
                slideNumber: slideNum,
                slideTitle: title,
                locationType: "notes",
                text:
                  notesText.length > 200
                    ? notesText.slice(0, 200)
                    : notesText,
                context: createContextSnippet(
                  notesText,
                  match.index,
                  match.index + match[0].length,
                ),
              });
            }
          }
        }
      }
    }
  }

  return {
    query,
    totalHits: hits.length,
    hits,
  };
}

// =============================================================================
// Tool Handlers
// =============================================================================

/**
 * get_presentation_info handler
 */
export const getPresentationInfoHandler = localFileToolHandler(
  {
    oldFormat: [
      ".ppt",
      "PowerPoint",
      ".pptx",
      "jszip/fast-xml-parser",
      "Microsoft PowerPoint",
    ],
    logPrefix: "PowerPoint情報取得",
  },
  async ({ content, filename }): Promise<ToolResult> => {
    const info = await getPresentationInfo(content, filename);

    const resultLines: string[] = [
      `# PowerPoint情報: ${info.filename}`,
      `スライド数: ${info.totalSlides}`,
      `総文字数: ${info.totalCharacters.toLocaleString()}`,
      "",
      "## スライド一覧",
    ];

    for (const slide of info.slides) {
      const elements: string[] = [];
      if (slide.textCount > 0) {
        elements.push(`テキスト${slide.textCount}`);
      }
      if (slide.imageCount > 0) {
        elements.push(`画像${slide.imageCount}`);
      }
      if (slide.tableCount > 0) {
        elements.push(`表${slide.tableCount}`);
      }
      if (slide.chartCount > 0) {
        elements.push(`グラフ${slide.chartCount}`);
      }

      const elementStr = elements.length > 0 ? elements.join(", ") : "空";

      resultLines.push("");
      resultLines.push(
        `### スライド ${slide.number} - "${slide.title}"`,
      );
      resultLines.push(`- 要素: ${elementStr}`);
      resultLines.push(`- 文字数: 約${slide.charCount}文字`);
      if (slide.hasNotes) {
        resultLines.push(`- ノート: あり (${slide.notesLength}文字)`);
      }
    }

    resultLines.push("");
    resultLines.push("---");
    resultLines.push("スライド取得: `get_slides_content` を使用");
    resultLines.push("検索: `search_presentation` を使用");

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * get_slides_content handler
 */
export const getSlidesContentHandler = localFileToolHandler(
  {
    oldFormat: [
      ".ppt",
      "PowerPoint",
      ".pptx",
      "jszip/fast-xml-parser",
      "Microsoft PowerPoint",
    ],
    logPrefix: "PowerPointスライド取得",
  },
  async ({ content, filename, args }): Promise<ToolResult> => {
    const slidesSpec = (args.slides as string) || "1-10";
    const maxSlides = (args.max_slides as number) || DEFAULT_MAX_SLIDES;
    const includeNotes = args.include_notes !== false;
    const includeTables = args.include_tables !== false;

    const result = await getSlidesContent(content, {
      slidesSpec,
      maxSlides,
      includeNotes,
      includeTables,
    });

    const resultLines: string[] = [
      `# ${filename}`,
      `取得スライド: ${result.requestedSlides} (全${result.totalSlides}スライド)`,
      `返却: ${result.returnedSlides}スライド`,
      "",
    ];

    for (const slide of result.slides) {
      resultLines.push(`## スライド ${slide.number} - "${slide.title}"`);
      resultLines.push("");

      if (slide.textContent.length > 0) {
        for (const text of slide.textContent) {
          resultLines.push(text);
        }
        resultLines.push("");
      }

      if (slide.tableContent.length > 0) {
        resultLines.push("### 表");
        for (const row of slide.tableContent) {
          resultLines.push(row);
        }
        resultLines.push("");
      }

      if (slide.notes) {
        resultLines.push("### ノート");
        resultLines.push(slide.notes);
        resultLines.push("");
      }

      if (
        slide.textContent.length === 0 &&
        slide.tableContent.length === 0
      ) {
        resultLines.push("[このスライドにテキストは含まれていません]");
        resultLines.push("");
      }

      resultLines.push("---");
      resultLines.push("");
    }

    if (result.hasMore) {
      resultLines.push("まだ続きがあります。次を取得するには:");
      resultLines.push(
        `\`slides="${result.endSlide + 1}-${result.endSlide + maxSlides}"\` を指定してください。`,
      );
    }

    return formatToolSuccess(resultLines.join("\n"));
  },
);

/**
 * search_presentation handler
 */
export const searchPresentationHandler = localFileToolHandler(
  {
    oldFormat: [
      ".ppt",
      "PowerPoint",
      ".pptx",
      "jszip/fast-xml-parser",
      "Microsoft PowerPoint",
    ],
    logPrefix: "PowerPoint検索",
  },
  async ({ content, args }): Promise<ToolResult> => {
    const query = (args.query as string) || "";
    const caseSensitive = (args.case_sensitive as boolean) || false;
    const maxHits = (args.max_hits as number) || 50;
    const includeNotes = args.include_notes !== false;

    if (!query) {
      return formatToolError(
        "エラー: query（検索キーワード）を指定してください。",
      );
    }

    const result = await searchPresentation(content, query, {
      caseSensitive,
      maxHits,
      includeNotes,
    });

    const resultLines: string[] = [
      `# 検索結果: "${result.query}"`,
      `ヒット数: ${result.totalHits}`,
      "",
    ];

    if (result.hits.length > 0) {
      for (const hit of result.hits) {
        const locationLabel: Record<string, string> = {
          title: "タイトル",
          text: "テキスト",
          table: "表",
          notes: "ノート",
        };

        resultLines.push(
          `## スライド ${hit.slideNumber} - "${hit.slideTitle}"`,
        );
        resultLines.push(
          `場所: ${locationLabel[hit.locationType] || hit.locationType}`,
        );
        resultLines.push(`コンテキスト: ${hit.context}`);
        resultLines.push("");
      }
    } else {
      resultLines.push("検索結果はありませんでした。");
    }

    return formatToolSuccess(resultLines.join("\n"));
  },
);
