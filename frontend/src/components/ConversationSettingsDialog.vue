<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { createConversation, updateConversation } from '@/api/chat'
import { EMBEDDING_MODES } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  conversation: { type: Object, default: null },
  knowledgeBases: { type: Array, default: () => [] },
  options: { type: Object, default: null },
  pendingQuestion: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'saved'])

const saving = ref(false)
const formRef = ref()

const form = reactive({
  title: '',
  kb_ids: [],
  model_name: '',
  temperature: 0.3,
  top_p: 0.8,
  max_tokens: 1024,
  history_rounds: 5,
})

const isEdit = computed(() => Boolean(props.conversation))
const title = computed(() => (isEdit.value ? '会话设置' : '新建对话'))

const limits = computed(() => ({
  temperature: props.options?.limits?.temperature || [0, 2],
  top_p: props.options?.limits?.top_p || [0, 1],
  max_tokens: props.options?.limits?.max_tokens || [32, 4096],
  history_rounds: props.options?.limits?.history_rounds || [0, 20],
}))

const models = computed(() => props.options?.models || [])
const defaults = computed(() => ({
  model_name: props.options?.defaults?.model_name || 'deepseekv4-flash',
  temperature: props.options?.defaults?.temperature ?? 0.3,
  top_p: props.options?.defaults?.top_p ?? 0.8,
  max_tokens: props.options?.defaults?.max_tokens ?? 1024,
  history_rounds: props.options?.defaults?.history_rounds ?? 5,
}))

const rules = {
  kb_ids: [
    {
      validator: (_rule, value, callback) => {
        if (!value || !value.length) callback(new Error('请至少选择一个知识库'))
        else callback()
      },
      trigger: 'change',
    },
  ],
}

function modeLabel(mode) {
  return EMBEDDING_MODES.find((item) => item.value === mode)?.label || mode
}

function modelLabel(name) {
  return models.value.find((item) => item.name === name)?.label || name
}

function initialize() {
  const conversation = props.conversation
  if (conversation) {
    Object.assign(form, {
      title: conversation.title || '',
      kb_ids: [...(conversation.kb_ids || [])],
      model_name: conversation.model_name,
      temperature: conversation.temperature,
      top_p: conversation.top_p,
      max_tokens: conversation.max_tokens,
      history_rounds: conversation.history_rounds,
    })
  } else {
    Object.assign(form, {
      title: '',
      kb_ids: props.knowledgeBases.length ? [props.knowledgeBases[0].id] : [],
      model_name: defaults.value.model_name,
      temperature: defaults.value.temperature,
      top_p: defaults.value.top_p,
      max_tokens: defaults.value.max_tokens,
      history_rounds: defaults.value.history_rounds,
    })
  }
  formRef.value?.clearValidate()
}

watch(
  () => props.modelValue,
  (visible) => {
    if (visible) initialize()
  },
)

function close() {
  if (!saving.value) emit('update:modelValue', false)
}

async function submit() {
  if (saving.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  const payload = {
    title: form.title.trim() || undefined,
    kb_ids: [...form.kb_ids],
    model_name: form.model_name,
    temperature: form.temperature,
    top_p: form.top_p,
    max_tokens: form.max_tokens,
    history_rounds: form.history_rounds,
  }

  saving.value = true
  try {
    const conversation = isEdit.value
      ? await updateConversation(props.conversation.id, payload)
      : await createConversation(payload)
    ElMessage.success(isEdit.value ? '会话设置已更新' : '对话已创建')
    emit('saved', { conversation, isNew: !isEdit.value })
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(toErrorMessage(error))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="640px"
    top="6vh"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="settings">
      <div v-if="pendingQuestion" class="settings__pending">
        <span>创建后将自动提问</span>
        <p>{{ pendingQuestion }}</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="对话名称">
          <el-input
            v-model="form.title"
            maxlength="120"
            :placeholder="isEdit ? '输入会话名称' : '留空则根据首个问题自动命名'"
          />
        </el-form-item>

        <el-form-item label="知识库范围" prop="kb_ids">
          <el-select
            v-model="form.kb_ids"
            multiple
            collapse-tags
            collapse-tags-tooltip
            placeholder="选择一个或多个知识库"
            style="width: 100%"
          >
            <el-option
              v-for="kb in knowledgeBases"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            >
              <div class="settings__kb-option">
                <span class="settings__kb-name">
                  {{ kb.name }}
                  <el-tag v-if="kb.is_public" size="small" effect="plain">公共</el-tag>
                </span>
                <span class="settings__kb-meta">
                  {{ modeLabel(kb.embedding_mode) }} · {{ kb.document_count }} 篇
                </span>
              </div>
            </el-option>
          </el-select>
          <p v-if="!knowledgeBases.length" class="settings__hint">
            暂无知识库，请先前往「知识库」创建并上传文档。
          </p>
        </el-form-item>

        <el-form-item label="大模型">
          <el-select v-model="form.model_name" style="width: 100%">
            <el-option
              v-for="model in models"
              :key="model.name"
              :label="model.label"
              :value="model.name"
              :disabled="!model.available"
            >
              <div class="settings__model-option">
                <span>{{ model.label }}</span>
                <el-tag v-if="!model.available" size="small" type="info">未配置 Key</el-tag>
                <el-tag v-else-if="model.default" size="small" effect="plain">默认</el-tag>
                <span v-else class="settings__provider">{{ model.provider }}</span>
              </div>
            </el-option>
          </el-select>
        </el-form-item>

        <div class="settings__params">
          <div class="settings__param-head">
            <span>推理参数</span>
            <small>影响回答的稳定性、发散程度与上下文长度</small>
          </div>

          <div class="settings__param">
            <div class="settings__param-label">
              <span>温度 Temperature</span>
              <b>{{ form.temperature.toFixed(1) }}</b>
            </div>
            <el-slider
              v-model="form.temperature"
              :min="limits.temperature[0]"
              :max="limits.temperature[1]"
              :step="0.1"
            />
            <p>越低越严谨，政策类问题建议 0.1 - 0.4。</p>
          </div>

          <div class="settings__param">
            <div class="settings__param-label">
              <span>Top P</span>
              <b>{{ form.top_p.toFixed(2) }}</b>
            </div>
            <el-slider
              v-model="form.top_p"
              :min="limits.top_p[0] || 0.01"
              :max="limits.top_p[1]"
              :step="0.01"
            />
            <p>控制候选词采样范围，通常保持 0.8 左右。</p>
          </div>

          <div class="settings__param">
            <div class="settings__param-label">
              <span>最长输出 Token</span>
              <b>{{ form.max_tokens }}</b>
            </div>
            <el-slider
              v-model="form.max_tokens"
              :min="limits.max_tokens[0]"
              :max="limits.max_tokens[1]"
              :step="32"
            />
          </div>

          <div class="settings__param">
            <div class="settings__param-label">
              <span>历史对话轮数</span>
              <b>{{ form.history_rounds }} 轮</b>
            </div>
            <el-slider
              v-model="form.history_rounds"
              :min="limits.history_rounds[0]"
              :max="limits.history_rounds[1]"
              :step="1"
            />
            <p>0 表示不携带历史；默认保留最近 5 轮对话。</p>
          </div>
        </div>
      </el-form>
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">
        {{ isEdit ? '保存设置' : '创建并开始' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.settings__pending {
  padding: 12px 15px;
  margin-bottom: 18px;
  background: linear-gradient(120deg, #f0efff, #effaf8);
  border: 1px solid rgba(91, 91, 214, 0.1);
  border-radius: 12px;
}

.settings__pending span {
  color: var(--atf-brand);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

.settings__pending p {
  margin-top: 5px;
  color: var(--atf-ink-soft);
  font-size: 13px;
  line-height: 1.6;
}

.settings__kb-option,
.settings__model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 14px;
}

.settings__kb-name {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.settings__kb-meta,
.settings__provider {
  color: var(--atf-muted);
  font-size: 11px;
}

.settings__hint {
  margin-top: 8px;
  color: var(--el-color-danger);
  font-size: 12px;
}

.settings__params {
  padding: 16px 18px 6px;
  margin-top: 6px;
  background: #faf9ff;
  border: 1px solid var(--atf-line);
  border-radius: 16px;
}

.settings__param-head {
  display: flex;
  flex-direction: column;
  margin-bottom: 8px;
  gap: 3px;
}

.settings__param-head span {
  font-size: 13px;
  font-weight: 700;
}

.settings__param-head small {
  color: var(--atf-muted);
  font-size: 11px;
}

.settings__param {
  padding: 8px 0;
}

.settings__param-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2px;
  color: var(--atf-ink-soft);
  font-size: 12px;
}

.settings__param-label b {
  color: var(--atf-brand-deep);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.settings__param p {
  color: var(--atf-muted);
  font-size: 10.5px;
  line-height: 1.5;
}

.settings__param :deep(.el-slider) {
  --el-slider-height: 4px;
}
</style>
