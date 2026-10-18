<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Document,
  EditPen,
  Files,
  Lock,
  Refresh,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'

import {
  deleteDocument,
  getKnowledgeBase,
  listDocuments,
  reindexDocument,
} from '@/api/knowledge'
import KnowledgeBaseDialog from '@/components/KnowledgeBaseDialog.vue'
import UploadDocumentDialog from '@/components/UploadDocumentDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { DOCUMENT_STATUS, EMBEDDING_MODES } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, formatFileSize, truncate } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const knowledgeBase = ref(null)
const documents = ref([])
const loading = ref(true)
const refreshing = ref(false)
const uploadVisible = ref(false)
const editVisible = ref(false)
const actionLoading = ref({})

const kbId = computed(() => Number(route.params.id))

const canManage = computed(
  () =>
    Boolean(knowledgeBase.value) &&
    Number(knowledgeBase.value.owner_id) === Number(auth.user?.id),
)

const stats = computed(() => {
  const completed = documents.value.filter((doc) => doc.status === 'completed').length
  const running = documents.value.filter((doc) =>
    ['pending', 'processing'].includes(doc.status),
  ).length
  const chunks = documents.value.reduce((total, doc) => total + (doc.chunk_count || 0), 0)
  return [
    { label: '文档总数', value: documents.value.length, tone: 'violet' },
    { label: '分块总数', value: chunks, tone: 'mint' },
    { label: '已就绪', value: completed, tone: 'blue' },
    { label: '解析中', value: running, tone: 'peach' },
  ]
})

const hasRunning = computed(() =>
  documents.value.some((doc) => ['pending', 'processing'].includes(doc.status)),
)

const modeLabel = computed(() => {
  const mode = knowledgeBase.value?.embedding_mode
  return EMBEDDING_MODES.find((item) => item.value === mode)?.label || mode || '—'
})

let pollTimer = null
let loadToken = 0

function syncPolling() {
  if (hasRunning.value && !pollTimer) {
    pollTimer = setInterval(() => refreshDocuments(true), 3000)
  } else if (!hasRunning.value && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
})

watch(
  kbId,
  (id) => {
    if (id) loadAll()
  },
  { immediate: true },
)

async function loadAll() {
  const token = ++loadToken
  loading.value = true
  try {
    const [kb, docs] = await Promise.all([getKnowledgeBase(kbId.value), listDocuments(kbId.value)])
    if (token !== loadToken) return
    knowledgeBase.value = kb
    documents.value = docs
    knowledgeBase.value.document_count = docs.length
    syncPolling()
  } catch (error) {
    if (token !== loadToken) return
    ElMessage.error(toErrorMessage(error, '知识库加载失败'))
    router.replace('/knowledge')
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function refreshDocuments(silent = false) {
  if (!silent) refreshing.value = true
  try {
    documents.value = await listDocuments(kbId.value)
    if (knowledgeBase.value) {
      knowledgeBase.value.document_count = documents.value.length
    }
    syncPolling()
  } catch (error) {
    if (!silent) ElMessage.error(toErrorMessage(error, '文档列表刷新失败'))
  } finally {
    refreshing.value = false
  }
}

async function handleUploaded(document) {
  documents.value.unshift(document)
  syncPolling()
}

function handleKnowledgeBaseSaved({ knowledgeBase: updated }) {
  knowledgeBase.value = updated
}

function fileMeta(document) {
  const type = (document.file_type || '').toLowerCase()
  if (type === 'pdf') return { label: 'PDF', className: 'file-pdf' }
  if (type === 'ppt' || type === 'pptx') return { label: 'PPT', className: 'file-ppt' }
  if (type === 'md' || type === 'markdown') return { label: 'MD', className: 'file-md' }
  if (type === 'doc' || type === 'docx') return { label: 'DOC', className: 'file-doc' }
  return { label: 'IMG', className: 'file-img' }
}

function statusMeta(status) {
  return DOCUMENT_STATUS[status] || { label: status || '未知', type: 'info' }
}

function isBusy(document) {
  return ['pending', 'processing'].includes(document.status)
}

function setActionLoading(id, value) {
  actionLoading.value = { ...actionLoading.value, [id]: value }
}

async function handleReindex(document) {
  if (!canManage.value || isBusy(document)) return
  setActionLoading(document.id, true)
  try {
    const updated = await reindexDocument(document.id)
    const index = documents.value.findIndex((item) => item.id === updated.id)
    if (index >= 0) documents.value.splice(index, 1, updated)
    ElMessage.success('已提交重新解析')
    syncPolling()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '重新解析失败'))
  } finally {
    setActionLoading(document.id, false)
  }
}

async function handleDelete(document) {
  if (!canManage.value) return
  try {
    await ElMessageBox.confirm(
      `删除「${document.original_name}」及其全部分块与向量数据？该操作不可恢复。`,
      '删除文档',
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

  setActionLoading(document.id, true)
  try {
    await deleteDocument(document.id)
    documents.value = documents.value.filter((item) => item.id !== document.id)
    if (knowledgeBase.value) {
      knowledgeBase.value.document_count = documents.value.length
    }
    ElMessage.success('文档已删除')
    syncPolling()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '删除文档失败'))
  } finally {
    setActionLoading(document.id, false)
  }
}

function previewChunks(document) {
  router.push(`/documents/${document.id}/chunks`)
}
</script>

<template>
  <div class="atf-page">
    <div class="atf-page-inner">
      <RouterLink class="detail__back" to="/knowledge">
        <el-icon><ArrowLeft /></el-icon>
        返回知识库
      </RouterLink>

      <div v-if="loading" class="detail__skeleton atf-card">
        <el-skeleton :rows="6" animated />
      </div>

      <template v-else-if="knowledgeBase">
        <header class="detail__head">
          <div class="detail__identity">
            <span class="detail__icon">{{ knowledgeBase.name.slice(0, 1) }}</span>
            <div class="detail__copy">
              <div class="detail__title-row">
                <h1>{{ knowledgeBase.name }}</h1>
                <span class="detail__mode">{{ modeLabel }}</span>
              </div>
              <p>{{ knowledgeBase.description || '暂无描述' }}</p>
              <div class="detail__chips">
                <span>切块 {{ knowledgeBase.chunk_size }}</span>
                <span>重叠 {{ knowledgeBase.chunk_overlap }}</span>
                <span>更新于 {{ formatDateTime(knowledgeBase.updated_at) }}</span>
              </div>
            </div>
          </div>

          <div class="detail__actions">
            <el-button :loading="refreshing" circle title="刷新" @click="refreshDocuments(false)">
              <el-icon><Refresh /></el-icon>
            </el-button>
            <template v-if="canManage">
              <el-button @click="editVisible = true">
                <el-icon><EditPen /></el-icon>
                编辑信息
              </el-button>
              <el-button type="primary" @click="uploadVisible = true">
                <el-icon><UploadFilled /></el-icon>
                上传文档
              </el-button>
            </template>
          </div>
        </header>

        <div v-if="!canManage" class="detail__readonly">
          <el-icon><Lock /></el-icon>
          <span>
            这是其他账号创建的公共知识库，你可以在对话中检索，但不能修改文档与分块。
          </span>
        </div>

        <div class="detail__stats">
          <div
            v-for="item in stats"
            :key="item.label"
            class="detail__stat"
            :class="`tone-${item.tone}`"
          >
            <strong>{{ item.value }}</strong>
            <span>{{ item.label }}</span>
          </div>
          <div v-if="hasRunning" class="detail__parsing">
            <i />
            文档正在后台解析，列表将自动刷新
          </div>
        </div>

        <section class="detail__docs atf-card">
          <div class="detail__docs-head">
            <div>
              <h2>文档列表</h2>
              <p>
                {{
                  canManage
                    ? '预览分块、调整解析状态，或移除不再使用的资料。'
                    : '公共知识库文档仅供查看与检索。'
                }}
              </p>
            </div>
            <span class="detail__docs-count">{{ documents.length }} 篇</span>
          </div>

          <div v-if="!documents.length" class="detail__empty">
            <div class="detail__empty-icon">
              <el-icon><Files /></el-icon>
            </div>
            <strong>还没有上传文档</strong>
            <span>
              {{
                canManage
                  ? '支持 PDF、PPT、Word、Markdown 与图片，上传后自动解析入库。'
                  : '该知识库由其他账号维护，暂无可检索的文档。'
              }}
            </span>
            <el-button v-if="canManage" type="primary" @click="uploadVisible = true">
              <el-icon><UploadFilled /></el-icon>
              上传第一篇文档
            </el-button>
          </div>

          <div v-else class="detail__table-wrap atf-scrollbar">
            <el-table :data="documents" row-key="id" class="detail__table">
              <el-table-column label="文档" min-width="280">
                <template #default="{ row }">
                  <div class="doc-cell">
                    <span class="doc-cell__icon" :class="fileMeta(row).className">
                      {{ fileMeta(row).label }}
                    </span>
                    <div class="doc-cell__copy">
                      <strong :title="row.original_name">{{ row.original_name }}</strong>
                      <span class="doc-cell__meta">
                        {{ formatFileSize(row.file_size) }}
                        <em class="doc-cell__version">v{{ row.version || 1 }}</em>
                        <em
                          v-if="row.update_action === 'updated'"
                          class="doc-cell__action is-updated"
                        >
                          已更新
                        </em>
                        <em
                          v-else-if="row.update_action === 'reindexed'"
                          class="doc-cell__action"
                        >
                          已重建
                        </em>
                        <em v-if="row.parser === 'mineru'" class="doc-cell__action is-mineru">
                          MinerU
                        </em>
                      </span>
                    </div>
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="状态" width="120">
                <template #default="{ row }">
                  <el-tooltip
                    :disabled="row.status !== 'failed' || !row.error_message"
                    :content="row.error_message || ''"
                    placement="top"
                  >
                    <span
                      class="status-pill"
                      :class="[`status-${row.status}`, { 'is-running': isBusy(row) }]"
                    >
                      <i />
                      {{ statusMeta(row.status).label }}
                    </span>
                  </el-tooltip>
                </template>
              </el-table-column>

              <el-table-column label="分块" width="90" align="center">
                <template #default="{ row }">
                  <span class="doc-chunk">{{ row.chunk_count || 0 }}</span>
                </template>
              </el-table-column>

              <el-table-column label="解析参数" width="150">
                <template #default="{ row }">
                  <div class="doc-params">
                    <span>{{ row.chunk_size }} / {{ row.chunk_overlap }}</span>
                    <small>{{ row.embedding_mode === 'hybrid' ? '混合' : '稠密' }}</small>
                  </div>
                </template>
              </el-table-column>

              <el-table-column label="更新时间" width="150">
                <template #default="{ row }">
                  <span class="doc-time">{{ formatDateTime(row.updated_at) }}</span>
                </template>
              </el-table-column>

              <el-table-column label="操作" width="240" fixed="right">
                <template #default="{ row }">
                  <div class="doc-actions">
                    <el-button
                      text
                      size="small"
                      :disabled="!row.chunk_count"
                      @click="previewChunks(row)"
                    >
                      <el-icon><View /></el-icon>
                      分块预览
                    </el-button>
                    <template v-if="canManage">
                      <el-button
                        text
                        size="small"
                        :loading="actionLoading[row.id]"
                        :disabled="isBusy(row)"
                        @click="handleReindex(row)"
                      >
                        <el-icon><Refresh /></el-icon>
                        重新解析
                      </el-button>
                      <el-dropdown trigger="click">
                        <el-button text size="small">更多</el-button>
                        <template #dropdown>
                          <el-dropdown-menu>
                            <el-dropdown-item @click="handleDelete(row)">
                              <el-icon><Document /></el-icon>
                              删除文档
                            </el-dropdown-item>
                          </el-dropdown-menu>
                        </template>
                      </el-dropdown>
                    </template>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </section>
      </template>
    </div>

    <UploadDocumentDialog
      v-if="canManage"
      v-model="uploadVisible"
      :knowledge-base="knowledgeBase"
      @uploaded="handleUploaded"
    />
    <KnowledgeBaseDialog
      v-if="canManage"
      v-model="editVisible"
      :knowledge-base="knowledgeBase"
      @saved="handleKnowledgeBaseSaved"
    />
  </div>
</template>

<style scoped>
.detail__back {
  display: inline-flex;
  align-items: center;
  color: var(--atf-muted);
  font-size: 11.5px;
  font-weight: 600;
  gap: 5px;
  transition: color 0.2s ease;
}

.detail__back:hover {
  color: var(--atf-brand);
}

.detail__skeleton {
  padding: 28px;
  margin-top: 20px;
}

.detail__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-top: 20px;
  gap: 24px;
}

.detail__identity {
  display: flex;
  min-width: 0;
  gap: 16px;
}

.detail__icon {
  display: grid;
  flex: none;
  width: 56px;
  height: 56px;
  color: #fff;
  font-size: 22px;
  font-weight: 750;
  background: linear-gradient(135deg, #5b5bd6, #8b7cf6 62%, #2dd4bf 140%);
  border-radius: 17px;
  box-shadow: 0 12px 28px rgba(91, 91, 214, 0.22);
  place-items: center;
}

.detail__title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail__title-row h1 {
  font-size: clamp(24px, 3vw, 32px);
  font-weight: 800;
  letter-spacing: -0.03em;
}

.detail__mode {
  padding: 3px 9px;
  color: #137d6d;
  font-size: 9.5px;
  font-weight: 700;
  background: #e3faf5;
  border-radius: 999px;
}

.detail__copy > p {
  margin-top: 5px;
  color: var(--atf-muted);
  font-size: 12px;
  line-height: 1.65;
}

.detail__chips {
  display: flex;
  flex-wrap: wrap;
  margin-top: 9px;
  gap: 7px;
}

.detail__chips span {
  padding: 3px 9px;
  color: #7d809a;
  font-size: 9.5px;
  background: #f2f2f8;
  border-radius: 999px;
}

.detail__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
}

.detail__readonly {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  margin-top: 18px;
  color: #5f5a8a;
  font-size: 11px;
  line-height: 1.6;
  background: linear-gradient(120deg, #f4f3ff, #f0fbf8);
  border: 1px solid rgba(91, 91, 214, 0.16);
  border-radius: 12px;
  gap: 8px;
}

.detail__readonly .el-icon {
  flex: none;
  color: var(--atf-brand);
  font-size: 15px;
}

.detail__stats {
  display: flex;
  align-items: center;
  padding: 14px 18px;
  margin-top: 24px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 16px;
  gap: 34px;
}

.detail__stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.detail__stat strong {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.02em;
}

.detail__stat span {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.detail__stat.tone-violet strong {
  color: var(--atf-brand-deep);
}

.detail__stat.tone-mint strong {
  color: #13806f;
}

.detail__stat.tone-blue strong {
  color: #2563a8;
}

.detail__stat.tone-peach strong {
  color: #b15a3c;
}

.detail__parsing {
  display: inline-flex;
  align-items: center;
  padding: 5px 11px;
  margin-left: auto;
  color: #8a6a24;
  font-size: 9.5px;
  background: #fff8e8;
  border-radius: 999px;
  gap: 6px;
}

.detail__parsing i {
  width: 5px;
  height: 5px;
  background: #e0a52e;
  border-radius: 50%;
  animation: atf-pulse 1.4s ease-in-out infinite;
}

.detail__docs {
  padding: 22px 24px 12px;
  margin-top: 16px;
}

.detail__docs-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding-bottom: 14px;
}

.detail__docs-head h2 {
  font-size: 16px;
  font-weight: 750;
}

.detail__docs-head p {
  margin-top: 3px;
  color: var(--atf-muted);
  font-size: 10.5px;
}

.detail__docs-count {
  padding: 4px 10px;
  color: var(--atf-brand);
  font-size: 10px;
  font-weight: 650;
  background: var(--atf-brand-soft);
  border-radius: 999px;
}

.detail__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 56px 20px;
  text-align: center;
  gap: 6px;
}

.detail__empty-icon {
  display: grid;
  width: 54px;
  height: 54px;
  margin-bottom: 6px;
  color: var(--atf-brand);
  font-size: 24px;
  background: var(--atf-brand-soft);
  border-radius: 16px;
  place-items: center;
}

.detail__empty strong {
  font-size: 14px;
  font-weight: 700;
}

.detail__empty span {
  color: var(--atf-muted);
  font-size: 11px;
}

.detail__empty .el-button {
  margin-top: 12px;
}

.detail__table-wrap {
  overflow-x: auto;
}

.detail__table {
  --el-table-border-color: var(--atf-line);
  --el-table-header-bg-color: #fafaff;
  --el-table-row-hover-bg-color: #fbfbff;
  min-width: 960px;
  font-size: 11.5px;
}

.detail__table :deep(th.el-table__cell) {
  color: var(--atf-muted);
  font-size: 9.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.detail__table :deep(td.el-table__cell) {
  padding: 11px 0;
}

.doc-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.doc-cell__icon {
  display: grid;
  flex: none;
  width: 34px;
  height: 34px;
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.03em;
  border-radius: 9px;
  place-items: center;
}

.file-pdf {
  color: #b23a48;
  background: #ffecee;
}

.file-ppt {
  color: #b15a1c;
  background: #fff1e4;
}

.file-md {
  color: #2563a8;
  background: #e8f2ff;
}

.file-doc {
  color: #6846d8;
  background: #efeaff;
}

.file-img {
  color: #137d6d;
  background: #e3faf5;
}

.doc-cell__copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}

.doc-cell__copy strong {
  overflow: hidden;
  color: var(--atf-ink-soft);
  font-size: 11.5px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-cell__copy span {
  color: #a3a6bd;
  font-size: 9.5px;
}

.doc-cell__meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.doc-cell__version {
  padding: 0 6px;
  color: #7d809a;
  font-size: 9px;
  font-style: normal;
  font-weight: 650;
  background: #f2f2f8;
  border-radius: 999px;
}

.doc-cell__action {
  padding: 0 6px;
  color: #2563a8;
  font-size: 9px;
  font-style: normal;
  font-weight: 650;
  background: #e8f2ff;
  border-radius: 999px;
}

.doc-cell__action.is-updated {
  color: #137d6d;
  background: #e3faf5;
}

.doc-cell__action.is-mineru {
  color: #7a4bb3;
  background: #f1e9ff;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  font-size: 9.5px;
  font-weight: 650;
  border-radius: 999px;
  gap: 5px;
}

.status-pill i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.status-completed {
  color: #137d6d;
  background: #e3faf5;
}

.status-completed i {
  background: #2dd4bf;
}

.status-processing {
  color: #9a6a20;
  background: #fff8e8;
}

.status-processing i {
  background: #e0a52e;
}

.status-pending {
  color: #5f6280;
  background: #f1f2f7;
}

.status-pending i {
  background: #a3a6bd;
}

.status-skipped {
  color: #5f6280;
  background: #f1f2f7;
}

.status-skipped i {
  background: #a3a6bd;
}

.status-failed {
  color: #b03a47;
  background: #ffecef;
}

.status-failed i {
  background: #e05a68;
}

.status-pill.is-running i {
  animation: atf-pulse 1.2s ease-in-out infinite;
}

.doc-chunk {
  color: var(--atf-brand-deep);
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.doc-params {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.doc-params span {
  color: var(--atf-ink-soft);
  font-size: 10.5px;
  font-variant-numeric: tabular-nums;
}

.doc-params small {
  color: #a3a6bd;
  font-size: 9px;
}

.doc-time {
  color: #8b8eaa;
  font-size: 10px;
  white-space: nowrap;
}

.doc-actions {
  display: flex;
  align-items: center;
  gap: 1px;
}

.doc-actions :deep(.el-button) {
  padding: 5px 7px;
  color: var(--atf-muted);
  font-size: 10.5px;
}

.doc-actions :deep(.el-button:hover) {
  color: var(--atf-brand);
}

@media (max-width: 860px) {
  .detail__head {
    flex-direction: column;
  }

  .detail__actions {
    width: 100%;
  }

  .detail__actions .el-button--primary {
    flex: 1;
  }

  .detail__stats {
    flex-wrap: wrap;
    gap: 18px 30px;
  }

  .detail__parsing {
    width: 100%;
    margin-left: 0;
  }

  .detail__docs {
    padding: 18px 14px 8px;
  }
}
</style>
