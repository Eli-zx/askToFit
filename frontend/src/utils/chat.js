/**
 * 新建会话的默认知识库：优先公共的服装售前知识库，让默认检索直接落在业务库上。
 * 民法典等其他知识库不参与默认选择，用户需要时在输入框上方主动勾选。
 */
const DEFAULT_KB_NAME_KEYWORD = '服装'
export const MAX_KB_SELECTION = 10
export const TEMPERATURE_MIN = 0
export const TEMPERATURE_MAX = 2
export const TEMPERATURE_STEP = 0.1

function isUsableKnowledgeBase(kb) {
  if (!kb || kb.id === null || kb.id === undefined || kb.id === '') return false
  const id = Number(kb.id)
  return Number.isFinite(id) && id > 0
}

export function normalizeKbIds(ids) {
  if (!Array.isArray(ids)) return []
  const normalized = ids
    .map((id) => Number(id))
    .filter((id) => Number.isFinite(id) && id > 0)
  return [...new Set(normalized)]
}

export function pickDefaultKnowledgeBase(knowledgeBases) {
  if (!Array.isArray(knowledgeBases)) return null
  const candidates = knowledgeBases.filter(isUsableKnowledgeBase)
  if (!candidates.length) return null
  const apparel = candidates.filter((kb) =>
    String(kb.name || '').includes(DEFAULT_KB_NAME_KEYWORD),
  )
  const pool = apparel.length ? apparel : candidates
  return pool.find((kb) => kb.is_public === true) || pool[0]
}

export function clampTemperature(value, fallback = 0.3) {
  if (value === null || value === undefined || value === '') return fallback
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return fallback
  const clamped = Math.min(TEMPERATURE_MAX, Math.max(TEMPERATURE_MIN, numeric))
  // 0.1 的二进制精度会把 0.3 算成 0.30000000000000004，统一收敛到一位小数
  return Number(
    (Math.round(clamped / TEMPERATURE_STEP) * TEMPERATURE_STEP).toFixed(1),
  )
}

/**
 * 构造会话参数：显式选择优先，未选择时回退到默认知识库。
 * 温度和知识库来自输入框上方的工具条，创建后仍可通过会话设置调整。
 */
export function buildConversationPayload(knowledgeBases, options = {}) {
  const explicitIds = normalizeKbIds(options.kbIds)
  const fallback = pickDefaultKnowledgeBase(knowledgeBases)
  const kbIds = explicitIds.length
    ? explicitIds.slice(0, MAX_KB_SELECTION)
    : fallback
      ? [Number(fallback.id)]
      : []
  if (!kbIds.length) return null

  const payload = { kb_ids: kbIds }
  if (options.temperature !== undefined && options.temperature !== null) {
    payload.temperature = clampTemperature(options.temperature)
  }
  return payload
}

/**
 * 兼容旧调用：没有会话时用默认知识库建会话。
 * 模型与其余推理参数交给后端默认值，避免前后端各维护一份。
 */
export function buildDefaultConversationPayload(knowledgeBases) {
  return buildConversationPayload(knowledgeBases)
}
