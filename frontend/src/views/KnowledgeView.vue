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
import { formatDateTime, truncate } from '@/utils/format'

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
  return Number(kb.owner_id) === Number(auth.user?.id)
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
            <span class="knowledge-card__icon">{{ kb.name.slice(0, 1) }}</span>
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

.knowledge-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: 30px;
  gap: 12px;
}

.stat-card {
  display: flex;
  align-items: center;
  padding: 16px 18px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 16px;
  gap: 13px;
}

.stat-card__icon {
  display: grid;
  width: 38px;
  height: 38px;
  font-size: 17px;
  border-radius: 11px;
  place-items: center;
}

.stat-card--violet .stat-card__icon {
  color: #5b4bc4;
  background: #eeebff;
}

.stat-card--mint .stat-card__icon {
  color: #137d6d;
  background: #e3faf5;
}

.stat-card--blue .stat-card__icon {
  color: #2563a8;
  background: #e8f2ff;
}

.stat-card--peach .stat-card__icon {
  color: #b15a3c;
  background: #fff0eb;
}

.stat-card div {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.stat-card strong {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.2;
}

.stat-card span {
  color: var(--atf-muted);
  font-size: 10px;
}

.knowledge-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 24px;
  gap: 16px;
}

.knowledge-card {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 235px;
  padding: 20px 21px 17px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}

.knowledge-card:hover {
  border-color: rgba(91, 91, 214, 0.2);
  box-shadow: var(--atf-shadow-md);
  transform: translateY(-4px);
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
  color: #fff;
  font-size: 17px;
  font-weight: 750;
  background: linear-gradient(135deg, #5b5bd6, #8b7cf6 62%, #2dd4bf 140%);
  border-radius: 13px;
  box-shadow: 0 8px 18px rgba(91, 91, 214, 0.2);
  place-items: center;
}

.knowledge-card__public {
  padding: 3px 9px;
  color: #6846d8;
  font-size: 9.5px;
  font-weight: 700;
  background: #efeaff;
  border-radius: 999px;
}

.knowledge-card__mode {
  padding: 3px 9px;
  font-size: 9.5px;
  font-weight: 700;
  border-radius: 999px;
}

.mode-hybrid {
  color: #137d6d;
  background: #e3faf5;
}

.mode-dense {
  color: #2563a8;
  background: #e8f2ff;
}

.knowledge-card__menu {
  margin-left: auto;
}

.knowledge-card__menu button {
  width: 30px;
  height: 30px;
  color: #a6a9be;
  font-size: 15px;
  letter-spacing: 1px;
  background: transparent;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.knowledge-card__menu button:hover {
  color: var(--atf-brand);
  background: var(--atf-brand-soft);
}

.knowledge-card h3 {
  margin-top: 16px;
  overflow: hidden;
  font-size: 16px;
  font-weight: 750;
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
  color: #8b8eaa;
  font-size: 10.5px;
  gap: 4px;
}

.knowledge-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 12px;
  margin-top: auto;
  color: #aeb1c5;
  font-size: 9.5px;
  border-top: 1px dashed var(--atf-line);
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
  width: 64px;
  height: 64px;
  color: #fff;
  font-size: 26px;
  background: linear-gradient(135deg, #5b5bd6, #8b7cf6 60%, #2dd4bf 140%);
  border-radius: 21px;
  box-shadow: 0 16px 36px rgba(91, 91, 214, 0.22);
  place-items: center;
}

.knowledge-empty h2 {
  margin-top: 19px;
  font-size: 21px;
  font-weight: 750;
  letter-spacing: -0.02em;
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

  .knowledge-grid {
    grid-template-columns: 1fr;
  }
}
</style>
