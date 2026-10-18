<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Delete,
  EditPen,
  Files,
  Refresh,
  Search,
} from '@element-plus/icons-vue'

import {
  deleteChunk,
  getDocument,
  getKnowledgeBase,
  listChunks,
  updateChunk,
} from '@/api/knowledge'
import { useAuthStore } from '@/stores/auth'
import { DOCUMENT_STATUS, EMBEDDING_MODES } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, formatFileSize } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const document = ref(null)
const knowledgeBase = ref(null)
const chunks = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const keyword = ref('')
const appliedKeyword = ref('')
const loading = ref(true)
const chunksLoading = ref(false)
const refreshedAt = ref('')

const expanded = reactive({})
const parentExpanded = reactive({})
const editVisible = ref(false)
const editingChunk = ref(null)
const editContent = ref('')
const saving = ref(false)

const docId = computed(() => Number(route.params.id))

const canManage = computed(
  () =>
    Boolean(knowledgeBase.value) &&
    Number(knowledgeBase.value.owner_id) === Number(auth.user?.id),
)

const statusMeta = computed(
  () => DOCUMENT_STATUS[document.value?.status] || { label: document.value?.status || '—' },
)

const modeLabel = computed(() => {
  const mode = document.value?.embedding_mode
  return EMBEDDING_MODES.find((item) => item.value === mode)?.label || mode || '—'
})

const searchActive = computed(() => Boolean(appliedKeyword.value))

let loadToken = 0

watch(
  docId,
  (id) => {
    if (id) loadContext()
  },
  { immediate: true },
)

async function loadContext() {
  const token = ++loadToken
  loading.value = true
  try {
    const doc = await getDocument(docId.value)
    if (token !== loadToken) return
    document.value = doc
    const [kb] = await Promise.all([
      getKnowledgeBase(doc.kb_id),
      loadChunks(),
    ])
    if (token !== loadToken) return
    knowledgeBase.value = kb
    refreshedAt.value = formatDateTime(new Date())
  } catch (error) {
    if (token !== loadToken) return
    ElMessage.error(toErrorMessage(error, '分块数据加载失败'))
    router.replace('/knowledge')
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function loadChunks() {
  chunksLoading.value = true
  try {
    const data = await listChunks(docId.value, {
      page: page.value,
      page_size: pageSize.value,
      ...(appliedKeyword.value ? { keyword: appliedKeyword.value } : {}),
    })
    chunks.value = data.items || []
    total.value = data.total || 0
    if (document.value) document.value.chunk_count = data.total
    refreshedAt.value = formatDateTime(new Date())
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '分块加载失败'))
  } finally {
    chunksLoading.value = false
  }
}

async function search() {
  page.value = 1
  appliedKeyword.value = keyword.value.trim()
  await loadChunks()
}

function clearSearch() {
  if (!keyword.value) return
  keyword.value = ''
  search()
}

function toggleExpand(uid) {
  expanded[uid] = !expanded[uid]
}

function toggleParent(uid) {
  parentExpanded[uid] = !parentExpanded[uid]
}

function isLong(chunk) {
  return (chunk.content || '').length > 520
}

function openEdit(chunk) {
  if (!canManage.value) return
  editingChunk.value = chunk
  editContent.value = chunk.content
  editVisible.value = true
}

async function saveEdit() {
  if (!canManage.value) return
  const content = editContent.value.trim()
  if (!content) {
    ElMessage.warning('分块内容不能为空')
    return
  }
  saving.value = true
  try {
    const updated = await updateChunk(editingChunk.value.uid, content)
    const index = chunks.value.findIndex((item) => item.uid === updated.uid)
    if (index >= 0) chunks.value.splice(index, 1, updated)
    ElMessage.success('分块已更新，并同步刷新向量')
    editVisible.value = false
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '分块更新失败'))
  } finally {
    saving.value = false
  }
}

async function handleDelete(chunk) {
  if (!canManage.value) return
  try {
    await ElMessageBox.confirm(
      `删除分块 #${chunk.chunk_index + 1}？其向量数据也会一并移除，且不可恢复。`,
      '删除分块',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }

  try {
    await deleteChunk(chunk.uid)
    ElMessage.success('分块已删除')
    if (chunks.value.length === 1 && page.value > 1) {
      page.value -= 1
    }
    await loadChunks()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '删除分块失败'))
  }
}

function handlePageChange(value) {
  page.value = value
  loadChunks()
}

function handleSizeChange(value) {
  pageSize.value = value
  page.value = 1
  loadChunks()
}

function goBack() {
  if (document.value) router.push(`/knowledge/${document.value.kb_id}`)
  else router.back()
}
</script>

<template>
  <div class="atf-page">
    <div class="atf-page-inner">
      <button class="chunk__back" type="button" @click="goBack">
        <el-icon><ArrowLeft /></el-icon>
        返回文档列表
      </button>

      <div v-if="loading" class="chunk__skeleton atf-card">
        <el-skeleton :rows="7" animated />
      </div>

      <template v-else-if="document">
        <header class="chunk__head">
          <div class="chunk__identity">
            <span class="chunk__icon"><el-icon><Files /></el-icon></span>
            <div>
              <div class="chunk__crumbs">
                <RouterLink to="/knowledge">知识库</RouterLink>
                <span>/</span>
                <RouterLink :to="`/knowledge/${document.kb_id}`">
                  {{ knowledgeBase?.name || `KB ${document.kb_id}` }}
                </RouterLink>
                <span>/</span>
                <b>分块预览</b>
              </div>
              <h1>{{ document.original_name }}</h1>
              <div class="chunk__meta">
                <span class="chunk__status" :class="`is-${document.status}`">
                  {{ statusMeta.label }}
                </span>
                <span>{{ formatFileSize(document.file_size) }}</span>
                <span>切块 {{ document.chunk_size }} / 重叠 {{ document.chunk_overlap }}</span>
                <span>{{ modeLabel }}</span>
                <span>共 {{ document.chunk_count }} 个分块</span>
                <span v-if="!canManage" class="chunk__readonly">公共知识库 · 只读</span>
              </div>
            </div>
          </div>

          <div class="chunk__actions">
            <el-button :loading="chunksLoading" circle title="刷新" @click="loadChunks">
              <el-icon><Refresh /></el-icon>
            </el-button>
            <el-button @click="router.push(`/knowledge/${document.kb_id}`)">
              管理文档
            </el-button>
          </div>
        </header>

        <div class="chunk__toolbar">
          <div class="chunk__search">
            <el-input
              v-model="keyword"
              clearable
              placeholder="搜索分块内容"
              @keyup.enter="search"
              @clear="clearSearch"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-button type="primary" @click="search">搜索</el-button>
          </div>
          <div class="chunk__toolbar-meta">
            <span v-if="searchActive">“{{ appliedKeyword }}” 命中 {{ total }} 个分块</span>
            <span v-else>共 {{ total }} 个分块</span>
            <span class="chunk__dot">·</span>
            <span>更新于 {{ refreshedAt }}</span>
          </div>
        </div>

        <div v-if="chunksLoading" class="chunk__loading">
          <el-skeleton :rows="5" animated />
        </div>

        <div v-else-if="!chunks.length" class="chunk__empty atf-card">
          <el-icon><Files /></el-icon>
          <strong>{{ searchActive ? '没有匹配的分块' : '暂无分块' }}</strong>
          <span>
            {{ searchActive ? '尝试更换关键词，或清空搜索条件。' : '请返回文档列表重新解析该文档。' }}
          </span>
          <el-button v-if="searchActive" @click="clearSearch">清空搜索</el-button>
        </div>

        <div v-else class="chunk__list">
          <article
            v-for="chunk in chunks"
            :key="chunk.uid"
            class="chunk-card atf-rise"
          >
            <div class="chunk-card__head">
              <span class="chunk-card__index">
                #{{ String(chunk.chunk_index + 1).padStart(3, '0') }}
              </span>
              <div class="chunk-card__titles">
                <strong>{{ chunk.title || document.original_name }}</strong>
                <span>
                  {{ chunk.section || '正文' }}
                  <template v-if="chunk.page"> · 第 {{ chunk.page }} 页</template>
                </span>
              </div>
              <span class="chunk-card__chars">{{ chunk.char_count }} 字</span>
              <div v-if="canManage" class="chunk-card__actions">
                <el-button text size="small" @click="openEdit(chunk)">
                  <el-icon><EditPen /></el-icon>
                  编辑
                </el-button>
                <el-button text size="small" class="is-danger" @click="handleDelete(chunk)">
                  <el-icon><Delete /></el-icon>
                  删除
                </el-button>
              </div>
            </div>

            <p
              class="chunk-card__content"
              :class="{ 'is-clamped': isLong(chunk) && !expanded[chunk.uid] }"
            >
              {{ chunk.content }}
            </p>

            <button
              v-if="isLong(chunk)"
              class="chunk-card__more"
              type="button"
              @click="toggleExpand(chunk.uid)"
            >
              {{ expanded[chunk.uid] ? '收起内容' : '展开全文' }}
            </button>

            <div v-if="chunk.parent_content" class="chunk-card__parent">
              <button
                class="chunk-card__parent-toggle"
                type="button"
                @click="toggleParent(chunk.uid)"
              >
                <span class="chunk-card__parent-label">父块上下文</span>
                <span class="chunk-card__parent-meta">
                  #{{ (chunk.parent_index ?? 0) + 1 }} ·
                  {{ chunk.parent_content.length }} 字
                </span>
                <span class="chunk-card__parent-action">
                  {{ parentExpanded[chunk.uid] ? '收起' : '查看' }}
                </span>
              </button>
              <p v-if="parentExpanded[chunk.uid]" class="chunk-card__parent-content">
                {{ chunk.parent_content }}
              </p>
            </div>
          </article>
        </div>

        <div v-if="total > 0" class="chunk__pagination">
          <el-pagination
            :current-page="page"
            :page-size="pageSize"
            :page-sizes="[10, 20, 50]"
            :total="total"
            layout="total, sizes, prev, pager, next"
            background
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </template>
    </div>

    <el-dialog
      v-if="canManage"
      v-model="editVisible"
      title="编辑分块"
      width="680px"
      top="7vh"
      append-to-body
      destroy-on-close
    >
      <div class="edit">
        <div class="edit__meta" v-if="editingChunk">
          <span>#{{ editingChunk.chunk_index + 1 }}</span>
          <span>{{ editingChunk.title || document?.original_name }}</span>
          <span>{{ editingChunk.section || '正文' }}</span>
        </div>
        <el-input
          v-model="editContent"
          type="textarea"
          :rows="14"
          maxlength="8000"
          show-word-limit
          placeholder="输入分块内容"
        />
        <p class="edit__hint">
          保存后将重新生成该分块的向量、同步父块上下文并写入 Milvus，后续对话检索立即生效。
        </p>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存并同步</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.chunk__back {
  display: inline-flex;
  align-items: center;
  color: var(--atf-muted);
  font-size: 11.5px;
  font-weight: 600;
  background: transparent;
  border: 0;
  cursor: pointer;
  gap: 5px;
}

.chunk__back:hover {
  color: var(--atf-brand);
}

.chunk__skeleton {
  padding: 28px;
  margin-top: 20px;
}

.chunk__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-top: 18px;
  gap: 20px;
}

.chunk__identity {
  display: flex;
  min-width: 0;
  gap: 14px;
}

.chunk__icon {
  display: grid;
  flex: none;
  width: 48px;
  height: 48px;
  color: var(--atf-brand);
  font-size: 21px;
  background: var(--atf-brand-soft);
  border-radius: 14px;
  place-items: center;
}

.chunk__crumbs {
  display: flex;
  align-items: center;
  color: #a3a6bd;
  font-size: 9.5px;
  gap: 5px;
}

.chunk__crumbs a {
  color: var(--atf-muted);
}

.chunk__crumbs a:hover {
  color: var(--atf-brand);
}

.chunk__crumbs b {
  color: var(--atf-ink-soft);
}

.chunk__identity h1 {
  margin-top: 5px;
  overflow: hidden;
  font-size: clamp(20px, 2.6vw, 27px);
  font-weight: 800;
  letter-spacing: -0.025em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 8px;
  gap: 6px 12px;
}

.chunk__meta span {
  color: #8b8eaa;
  font-size: 9.5px;
}

.chunk__status {
  padding: 2px 8px;
  font-weight: 700;
  border-radius: 999px;
}

.chunk__status.is-completed {
  color: #137d6d;
  background: #e3faf5;
}

.chunk__status.is-processing,
.chunk__status.is-pending {
  color: #9a6a20;
  background: #fff8e8;
}

.chunk__status.is-failed {
  color: #b03a47;
  background: #ffecef;
}

.chunk__readonly {
  padding: 2px 8px;
  color: #6846d8 !important;
  font-weight: 700;
  background: #efeaff;
  border-radius: 999px;
}

.chunk__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
}

.chunk__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px;
  margin-top: 22px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 14px;
  gap: 16px;
}

.chunk__search {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chunk__search :deep(.el-input) {
  width: 280px;
}

.chunk__search :deep(.el-input__wrapper) {
  height: 36px;
  box-shadow: none;
  border: 1px solid var(--atf-line);
}

.chunk__toolbar-meta {
  display: flex;
  align-items: center;
  color: #a3a6bd;
  font-size: 9.5px;
  gap: 7px;
}

.chunk__dot {
  color: #d0d2e0;
}

.chunk__loading {
  padding: 30px 20px;
}

.chunk__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 64px 20px;
  margin-top: 14px;
  color: var(--atf-muted);
  text-align: center;
  gap: 6px;
}

.chunk__empty > .el-icon {
  margin-bottom: 8px;
  color: var(--atf-brand);
  font-size: 32px;
}

.chunk__empty strong {
  color: var(--atf-ink-soft);
  font-size: 14px;
}

.chunk__empty span {
  font-size: 11px;
}

.chunk__empty .el-button {
  margin-top: 10px;
}

.chunk__list {
  display: flex;
  flex-direction: column;
  margin-top: 14px;
  gap: 10px;
}

.chunk-card {
  padding: 16px 18px 15px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 15px;
  transition: all 0.22s ease;
}

.chunk-card:hover {
  border-color: rgba(91, 91, 214, 0.18);
  box-shadow: 0 8px 24px rgba(43, 38, 95, 0.06);
}

.chunk-card__head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.chunk-card__index {
  flex: none;
  padding: 3px 8px;
  color: var(--atf-brand);
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 9.5px;
  font-weight: 700;
  background: var(--atf-brand-soft);
  border-radius: 6px;
}

.chunk-card__titles {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 1px;
}

.chunk-card__titles strong {
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 12px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-card__titles span {
  color: #a3a6bd;
  font-size: 9.5px;
}

.chunk-card__chars {
  flex: none;
  color: #a3a6bd;
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
}

.chunk-card__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 2px;
}

.chunk-card__actions :deep(.el-button) {
  padding: 4px 7px;
  color: var(--atf-muted);
  font-size: 10px;
}

.chunk-card__actions :deep(.el-button:hover) {
  color: var(--atf-brand);
}

.chunk-card__actions :deep(.el-button.is-danger:hover) {
  color: var(--el-color-danger);
}

.chunk-card__content {
  margin-top: 10px;
  color: #5f6280;
  font-size: 12px;
  line-height: 1.85;
  white-space: pre-wrap;
  word-break: break-word;
}

.chunk-card__content.is-clamped {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 6;
}

.chunk-card__more {
  padding: 0;
  margin-top: 7px;
  color: var(--atf-brand);
  font-size: 10px;
  font-weight: 650;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.chunk-card__parent {
  padding: 9px 11px;
  margin-top: 10px;
  background: #faf9ff;
  border: 1px dashed var(--atf-line-strong);
  border-radius: 10px;
}

.chunk-card__parent-toggle {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 0;
  background: transparent;
  border: 0;
  cursor: pointer;
  gap: 8px;
}

.chunk-card__parent-label {
  color: #6846d8;
  font-size: 10px;
  font-weight: 700;
}

.chunk-card__parent-meta {
  flex: 1;
  color: var(--atf-muted);
  font-size: 9.5px;
  text-align: left;
}

.chunk-card__parent-action {
  color: var(--atf-brand);
  font-size: 9.5px;
  font-weight: 650;
}

.chunk-card__parent-content {
  margin-top: 8px;
  padding-top: 8px;
  color: #6f7290;
  font-size: 11px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  border-top: 1px dashed var(--atf-line);
}

.chunk__pagination {
  display: flex;
  justify-content: flex-end;
  padding: 18px 0 4px;
}

.edit__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  padding: 9px 12px;
  margin-bottom: 12px;
  background: #faf9ff;
  border: 1px solid var(--atf-line);
  border-radius: 9px;
  gap: 9px;
}

.edit__meta span {
  color: var(--atf-muted);
  font-size: 10px;
}

.edit__meta span:first-child {
  color: var(--atf-brand);
  font-weight: 700;
}

.edit__hint {
  margin-top: 9px;
  color: var(--atf-muted);
  font-size: 10px;
  line-height: 1.6;
}

@media (max-width: 760px) {
  .chunk__head {
    flex-direction: column;
  }

  .chunk__toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .chunk__search :deep(.el-input) {
    flex: 1;
    width: auto;
  }

  .chunk-card__head {
    flex-wrap: wrap;
  }

  .chunk-card__actions {
    width: 100%;
    justify-content: flex-end;
  }
}
</style>
