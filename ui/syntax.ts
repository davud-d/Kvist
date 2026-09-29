// The colours of highlighted code in the claude skin, by highlight.js scope (src/highlight.ts).
const KEYWORD = "#f92672"
const STRING = "#a6e22e"
const NUMBER = "#ae81ff"
const COMMENT = "#8b8fa3"
const NAME = "#fd971f"
const TYPE = "#66d9ef"

export function syntaxColor(scope: string | null): string | undefined {
  if (!scope) return undefined
  const kind = scope.split(".")[0] ?? ""
  switch (kind) {
    case "keyword":
    case "literal":
    case "built_in":
    case "selector-tag":
    case "doctag":
    case "meta":
      return KEYWORD
    case "string":
    case "regexp":
    case "template-string":
    case "addition":
      return STRING
    case "number":
    case "symbol":
    case "bullet":
      return NUMBER
    case "comment":
    case "quote":
      return COMMENT
    case "params":
      return NAME
    case "title":
      return scope.includes("class") ? STRING : NAME
    case "type":
    case "attr":
    case "attribute":
    case "property":
    case "selector-class":
    case "selector-id":
      return TYPE
    case "tag":
    case "name":
      return KEYWORD
    default:
      return undefined
  }
}
