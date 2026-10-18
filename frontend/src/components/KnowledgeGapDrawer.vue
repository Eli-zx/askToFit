<script setup>
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh, Setting } from '@element-plus/icons-vue'

import { listKnowledgeGaps } from '@/api/chat'
import { INTENT_META } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { formatDateTime, formatScore } from '@/utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])
const router = useRouter()

const loading = ref(false)
const error = ref('')
const items = ref([])

function openAdmin() {
  emit('update:modelValue', false)
  router.push('/knowledge-gaps')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = await listKnowledgeGaps({ limit: 50, status: 'open' })
  } catch (err) {
    error.value = toErrorMessage(err, '加载知识缺口失败')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) load()
  },
)
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    title="知识缺口"
    size="440px"
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="gaps">
      <div class="gaps__intro">
        <p>当知识库没有命中可靠资料时，系统会自动记录问题，方便后续补充文档。</p>
        <div class="gaps__intro-actions">
          <el-button text :loading="loading" @click="load">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
          <el-button text @click="openAdmin">
            <el-icon><Setting /></el-icon>
            运营台
          </el-button>
        </div>
      </div>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

      <div v-if="loading" class="gaps__loading">
        <el-skeleton :rows="6" animated />
      </div>

      <div v-else-if="!items.length" class="gaps__empty">
        <div class="gaps__empty-icon">◎</div>
        <strong>暂无知识缺口</strong>
        <span>说明近期问题的检索命中情况良好</span>
      </div>

      <div v-else class="gaps__list atf-scrollbar">
        <article v-for="item in items" :key="item.id" class="gaps__item">
          <div class="gaps__item-head">
            <span class="gaps__intent">
              {{ INTENT_META[item.intent]?.label || '综合咨询' }}
            </span>
            <span class="gaps__score">
              最高分 {{ formatScore(item.best_score) }}
            </span>
          </div>
          <p class="gaps__question">{{ item.question }}</p>
          <div class="gaps__meta">
            <span>{{ formatDateTime(item.created_at) }}</span>
            <span>知识库 {{ (item.kb_ids || []).join(' / ') || '—' }}</span>
          </div>
        </article>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.gaps {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.gaps__intro {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 12px 14px;
  margin-bottom: 16px;
  background: linear-gradient(120deg, #f4f3ff, #f0fbf8);
  border: 1px solid var(--atf-line);
  border-radius: 13px;
  gap: 10px;
}

.gaps__intro p {
  color: var(--atf-ink-soft);
  font-size: 11.5px;
  line-height: 1.7;
}

.gaps__intro-actions {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: flex-end;
}

.gaps__loading {
  padding: 8px;
}

.gaps__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 70px 20px;
  color: var(--atf-muted);
  text-align: center;
  gap: 6px;
}

.gaps__empty-icon {
  display: grid;
  width: 48px;
  height: 48px;
  margin-bottom: 8px;
  color: var(--atf-brand);
  font-size: 22px;
  background: var(--atf-brand-soft);
  border-radius: 50%;
  place-items: center;
}

.gaps__empty strong {
  color: var(--atf-ink-soft);
  font-size: 13px;
}

.gaps__empty span {
  font-size: 11px;
}

.gaps__list {
  display: flex;
  flex: 1;
  flex-direction: column;
  overflow-y: auto;
  gap: 9px;
}

.gaps__item {
  padding: 13px 14px;
  background: #fbfbff;
  border: 1px solid var(--atf-line);
  border-radius: 12px;
}

.gaps__item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.gaps__intent {
  padding: 2px 8px;
  color: var(--atf-brand-deep);
  font-size: 9.5px;
  font-weight: 700;
  background: var(--atf-brand-soft);
  border-radius: 999px;
}

.gaps__score {
  color: #a0a3b8;
  font-size: 9.5px;
  font-variant-numeric: tabular-nums;
}

.gaps__question {
  margin-top: 8px;
  color: var(--atf-ink-soft);
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.6;
}

.gaps__meta {
  display: flex;
  justify-content: space-between;
  margin-top: 7px;
  color: #aeb1c5;
  font-size: 9.5px;
}
</style>
