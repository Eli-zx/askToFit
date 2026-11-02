<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowRight,
  Collection,
  Cpu,
  Delete,
  Document,
  EditPen,
  Files,
  Plus,
  Refresh,
} from '@element-plus/icons-vue'

import { deleteKnowledgeBase, listKnowledgeBases } from '@/api/knowledge'
import KnowledgeBaseDialog from '@/components/KnowledgeBaseDialog.vue'
import { useAuthStore } from '@/stores/auth'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, knowledgeMark, truncate } from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()

const loading = ref(false)
const knowledgeBases = ref([])
const dialogVisible = ref(false)
const editing = ref(null)

const stats = computed(() => {
  const documents = knowledgeBases.value.reduce(
    (total, kb) => total + (kb.document_count || 0),
    0,
  )
  const hybrid = knowledgeBases.value.filter((kb) => kb.embedding_mode === 'hybrid').length
  return [
    { label: '知识库', value: knowledgeBases.value.length, icon: Collection, tone: 'violet' },
    { label: '已上传文档', value: documents, icon: Document, tone: 'mint' },
    { label: '混合检索', value: hybrid, icon: Cpu, tone: 'blue' },
    { label: '稠密检索', value: knowledgeBases.value.length - hybrid, icon: Files, tone: 'peach' },
  ]
})

async function loadKnowledgeBases() {
  loading.value = true
  try {
    knowledgeBases.value = await listKnowledgeBases()
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '知识库加载失败'))
  } finally {
    loading.value = false
  }
}

function canManage(kb) {
  // 管理员可管理全部知识库，其余用户只能管理自己创建的
  return Boolean(auth.user?.is_admin) || Number(kb.owner_id) === Number(auth.user?.id)
}

function openCreate() {
  editing.value = null
  dialogVisible.value = true
}

function openEdit(kb) {
  if (!canManage(kb)) return
  editing.value = kb
  dialogVisible.value = true
}

function handleSaved({ knowledgeBase, isNew }) {
  if (isNew) {
    knowledgeBases.value.unshift(knowledgeBase)
  } else {
    const index = knowledgeBases.value.findIndex((item) => item.id === knowledgeBase.id)
    if (index >= 0) knowledgeBases.value.splice(index, 1, knowledgeBase)
  }
}

async function handleDelete(kb) {
  if (!canManage(kb)) return
  try {
    await ElMessageBox.confirm(
      `删除「${kb.name}」将同时清除 ${kb.document_count || 0} 篇文档、分块与向量数据，且不可恢复。`,
      '删除知识库',
      {
        type: 'warning',
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }

  try {
    await deleteKnowledgeBase(kb.id)
    knowledgeBases.value = knowledgeBases.value.filter((item) => item.id !== kb.id)
    ElMessage.success('知识库已删除')
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '删除知识库失败'))
  }
}

function enterKnowledgeBase(kb) {
  router.push(`/knowledge/${kb.id}`)
}

function modeMeta(mode) {
  return mode === 'hybrid'
    ? { label: '混合检索', className: 'mode-hybrid' }
    : { label: '稠密检索', className: 'mode-dense' }
}

onMounted(loadKnowledgeBases)
</script>

<template>
  <div class="atf-page">
    <div class="atf-page-inner">
      <header class="knowledge-head">
        <div>
          <span class="atf-eyebrow">Knowledge workspace</span>
          <h1 class="atf-title">
            你的<span class="atf-gradient-text">知识库</span>
          </h1>
          <p class="atf-subtitle">
            按业务域组织文档。上传后自动切块、向量化，对话时按勾选范围检索。
          </p>
        </div>
        <div class="knowledge-head__actions">
          <el-button :loading="loading" circle title="刷新" @click="loadKnowledgeBases">
            <el-icon><Refresh /></el-icon>
          </el-button>
          <el-button type="primary" @click="openCreate">
            <el-icon><Plus /></el-icon>
            创建知识库
          </el-button>
        </div>
      </header>

      <div class="knowledge-stats">
        <div
          v-for="item in stats"
          :key="item.label"
          class="stat-card"
          :class="`stat-card--${item.tone}`"
        >
          <span class="stat-card__icon">
            <el-icon><component :is="item.icon" /></el-icon>
          </span>
          <div>
            <strong>{{ item.value }}</strong>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </div>

      <div v-if="loading" class="knowledge-grid">
        <div v-for="index in 3" :key="index" class="knowledge-card knowledge-card--skeleton">
          <el-skeleton :rows="4" animated />
        </div>
      </div>

      <div v-else-if="!knowledgeBases.length" class="knowledge-empty atf-card">
        <div class="knowledge-empty__orb">
          <el-icon><Collection /></el-icon>
        </div>
        <h2>从第一个知识库开始</h2>
        <p>
          建议按业务域拆分：退换货规则、发票说明、物流说明、支付结算与退款安全。
          每个知识库可上传 PDF、PPT、Word、Markdown 与图片。
        </p>
        <el-button type="primary" size="large" @click="openCreate">
          <el-icon><Plus /></el-icon>
          创建知识库
        </el-button>
      </div>

      <div v-else class="knowledge-grid">
        <article
          v-for="(kb, index) in knowledgeBases"
          :key="kb.id"
          class="knowledge-card atf-rise"
          :style="{ animationDelay: `${index * 45}ms` }"
          @click="enterKnowledgeBase(kb)"
        >
          <div class="knowledge-card__top">
            <span
              class="knowledge-card__icon"
              :class="{ 'is-code': knowledgeMark(kb.name).length > 1 }"
            >
              {{ knowledgeMark(kb.name) }}
            </span>
            <span v-if="kb.is_public" class="knowledge-card__public">公共</span>
            <span class="knowledge-card__mode" :class="modeMeta(kb.embedding_mode).className">
              {{ modeMeta(kb.embedding_mode).label }}
            </span>
            <el-dropdown
              v-if="canManage(kb)"
              trigger="click"
              class="knowledge-card__menu"
              @click.stop
            >
              <button type="button" @click.stop>···</button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="openEdit(kb)">
                    <el-icon><EditPen /></el-icon>
                    编辑信息
                  </el-dropdown-item>
                  <el-dropdown-item divided @click="handleDelete(kb)">
                    <el-icon><Delete /></el-icon>
                    删除知识库
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>

          <h3>{{ kb.name }}</h3>
          <p>{{ truncate(kb.description || '暂无描述，点击进入管理文档。', 72) }}</p>

          <div class="knowledge-card__meta">
            <span>
              <el-icon><Document /></el-icon>
              {{ kb.document_count || 0 }} 篇文档
            </span>
            <span>
              <el-icon><Cpu /></el-icon>
              {{ kb.chunk_size }} / {{ kb.chunk_overlap }}
            </span>
          </div>

          <div class="knowledge-card__foot">
            <span>{{ formatDateTime(kb.updated_at) }}</span>
            <span class="knowledge-card__enter">
              {{ canManage(kb) ? '管理文档' : '查看文档' }}
              <el-icon><ArrowRight /></el-icon>
            </span>
          </div>
        </article>
      </div>
    </div>

    <KnowledgeBaseDialog
      v-model="dialogVisible"
      :knowledge-base="editing"
      @saved="handleSaved"
    />
  </div>
</template>

<style scoped>
.knowledge-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
}

.knowledge-head .atf-title {
  margin-top: 14px;
}

.knowledge-head .atf-subtitle {
  max-width: 580px;
  margin-top: 10px;
}

.knowledge-head__actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 10px;
}

/* 指标带：一条通栏细线，数字用衬线，分隔靠竖线而不是卡片 */
.knowledge-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: 34px;
  border-top: 1px solid var(--atf-line-strong);
  border-bottom: 1px solid var(--atf-line-strong);
}

.stat-card {
  display: block;
  padding: 18px 26px 16px;
  background: transparent;
  border: 0;
  border-radius: 0;
}

.stat-card + .stat-card {
  border-left: 1px solid var(--atf-line);
}

.stat-card:first-child {
  padding-left: 0;
}

.stat-card__icon {
  display: none;
}

.stat-card div {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stat-card strong {
  font-family: var(--atf-font-display);
  font-size: clamp(34px, 3.4vw, 46px);
  font-weight: 500;
  letter-spacing: -0.02em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.stat-card span {
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

/* 知识库列表改成通栏索引行，不用悬浮卡片 */
.knowledge-grid {
  display: flex;
  flex-direction: column;
  margin-top: 20px;
  gap: 0;
  border-top: 1px solid var(--atf-line);
}

.knowledge-card {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 20px 2px 18px;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--atf-line);
  border-radius: 0;
  cursor: pointer;
  transition: background 0.2s ease;
}

.knowledge-card:hover {
  background: var(--atf-wash);
  box-shadow: none;
  transform: none;
}

.knowledge-card--skeleton {
  cursor: default;
}

.knowledge-card__top {
  display: flex;
  align-items: center;
  gap: 9px;
}

.knowledge-card__icon {
  display: grid;
  width: 42px;
  height: 42px;
  color: var(--atf-bg);
  font-family: var(--atf-font-display);
  font-size: 17px;
  font-weight: 600;
  background: var(--atf-brand);
  border-radius: 1px;
  place-items: center;
}

.knowledge-card__icon.is-code {
  font-family: var(--atf-font-mono);
  font-size: 11.5px;
  letter-spacing: 0.08em;
}

.knowledge-card__public {
  padding: 2px 8px;
  color: var(--atf-brand-deep);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  letter-spacing: 0.12em;
  background: var(--atf-brand-soft);
  border-radius: 1px;
}

.knowledge-card__mode {
  padding: 2px 8px;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  border: 1px solid var(--atf-line);
  border-radius: 1px;
}

.mode-hybrid {
  color: var(--atf-mint);
  border-color: var(--atf-tone-mint-line);
}

.mode-dense {
  color: var(--atf-tone-blue);
  border-color: var(--atf-tone-blue-line);
}

.knowledge-card__menu {
  margin-left: auto;
}

.knowledge-card__menu button {
  width: 30px;
  height: 30px;
  color: var(--atf-muted);
  font-size: 15px;
  letter-spacing: 1px;
  background: transparent;
  border: 0;
  border-radius: var(--atf-radius-sm);
  cursor: pointer;
}

.knowledge-card__menu button:hover {
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
}

.knowledge-card h3 {
  margin-top: 12px;
  overflow: hidden;
  font-family: var(--atf-font-display);
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.015em;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-card > p {
  display: -webkit-box;
  margin-top: 7px;
  overflow: hidden;
  color: var(--atf-muted);
  font-size: 11.5px;
  line-height: 1.7;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.knowledge-card__meta {
  display: flex;
  align-items: center;
  margin-top: 14px;
  gap: 16px;
}

.knowledge-card__meta span {
  display: inline-flex;
  align-items: center;
  color: var(--atf-muted);
  font-size: 10.5px;
  gap: 4px;
}

.knowledge-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  margin-top: auto;
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9.5px;
  border-top: 1px solid var(--atf-line);
}

.knowledge-card__enter {
  display: inline-flex;
  align-items: center;
  color: var(--atf-brand);
  font-size: 10px;
  font-weight: 700;
  gap: 3px;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.knowledge-card:hover .knowledge-card__enter {
  opacity: 1;
}

.knowledge-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 76px 26px;
  margin-top: 24px;
  text-align: center;
}

.knowledge-empty__orb {
  display: grid;
  width: 62px;
  height: 62px;
  color: var(--atf-bg);
  font-size: 25px;
  background: var(--atf-ink);
  border-radius: 2px;
  place-items: center;
}

.knowledge-empty h2 {
  margin-top: 19px;
  font-family: var(--atf-font-display);
  font-size: 23px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.knowledge-empty p {
  max-width: 520px;
  margin-top: 9px;
  color: var(--atf-muted);
  font-size: 12px;
  line-height: 1.8;
}

.knowledge-empty .el-button {
  margin-top: 22px;
}

@media (max-width: 1080px) {
  .knowledge-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .knowledge-head {
    flex-direction: column;
    align-items: flex-start;
  }

  .knowledge-head__actions {
    width: 100%;
  }

  .knowledge-head__actions .el-button--primary {
    flex: 1;
  }

  .knowledge-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .stat-card {
    padding: 16px 16px 14px;
  }

  .stat-card:nth-child(odd) {
    padding-left: 0;
    border-left: 0;
  }

  .stat-card:nth-child(n + 3) {
    border-top: 1px solid var(--atf-line);
  }

  .stat-card strong {
    font-size: 34px;
  }

  .knowledge-grid {
    grid-template-columns: 1fr;
  }
}
</style>
