<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Cpu, Files } from '@element-plus/icons-vue'

import { createKnowledgeBase, updateKnowledgeBase } from '@/api/knowledge'
import { EMBEDDING_MODES } from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  knowledgeBase: { type: Object, default: null },
})

const emit = defineEmits(['update:modelValue', 'saved'])

const saving = ref(false)
const formRef = ref()

const form = reactive({
  name: '',
  description: '',
  embedding_mode: 'hybrid',
  chunk_size: 500,
  chunk_overlap: 80,
  is_public: false,
})

const isEdit = computed(() => Boolean(props.knowledgeBase))
const title = computed(() => (isEdit.value ? '编辑知识库' : '创建知识库'))

const rules = {
  name: [
    { required: true, message: '请输入知识库名称', trigger: 'blur' },
    { max: 100, message: '名称不超过 100 个字符', trigger: 'blur' },
  ],
  chunk_size: [
    { required: true, message: '请输入切块大小', trigger: 'blur' },
    { type: 'number', min: 100, max: 2000, message: '范围 100 - 2000', trigger: 'blur' },
  ],
  chunk_overlap: [
    {
      validator: (_rule, value, callback) => {
        if (value == null || value < 0) callback(new Error('重叠大小不能为负数'))
        else if (value >= form.chunk_size) callback(new Error('重叠大小必须小于切块大小'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    if (props.knowledgeBase) {
      Object.assign(form, {
        name: props.knowledgeBase.name,
        description: props.knowledgeBase.description || '',
        embedding_mode: props.knowledgeBase.embedding_mode,
        chunk_size: props.knowledgeBase.chunk_size,
        chunk_overlap: props.knowledgeBase.chunk_overlap,
        is_public: Boolean(props.knowledgeBase.is_public),
      })
    } else {
      Object.assign(form, {
        name: '',
        description: '',
        embedding_mode: 'hybrid',
        chunk_size: 500,
        chunk_overlap: 80,
        is_public: false,
      })
    }
    formRef.value?.clearValidate()
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

  saving.value = true
  try {
    const payload = isEdit.value
      ? {
          name: form.name.trim(),
          description: form.description.trim() || null,
          is_public: form.is_public,
        }
      : {
          name: form.name.trim(),
          description: form.description.trim() || null,
          embedding_mode: form.embedding_mode,
          chunk_size: form.chunk_size,
          chunk_overlap: form.chunk_overlap,
          is_public: form.is_public,
        }
    const knowledgeBase = isEdit.value
      ? await updateKnowledgeBase(props.knowledgeBase.id, payload)
      : await createKnowledgeBase(payload)
    ElMessage.success(isEdit.value ? '知识库已更新' : '知识库创建成功')
    emit('saved', { knowledgeBase, isNew: !isEdit.value })
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '保存失败'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="title"
    width="600px"
    top="7vh"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
      <el-form-item label="知识库名称" prop="name">
        <el-input v-model="form.name" maxlength="100" placeholder="例如：服装售前知识库" />
      </el-form-item>

      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          maxlength="500"
          show-word-limit
          placeholder="简要说明知识库覆盖的业务范围"
        />
      </el-form-item>

      <el-form-item label="可见范围">
        <div class="kb-public" :class="{ 'is-active': form.is_public }">
          <div class="kb-public__copy">
            <strong>公开知识库</strong>
            <small>
              开启后所有登录账号都能在对话中检索；只有你能编辑、上传和删除。
            </small>
          </div>
          <el-switch v-model="form.is_public" />
        </div>
      </el-form-item>

      <el-form-item label="检索模式">
        <div class="kb-modes" :class="{ 'is-disabled': isEdit }">
          <label
            v-for="mode in EMBEDDING_MODES"
            :key="mode.value"
            class="kb-mode"
            :class="{ 'is-active': form.embedding_mode === mode.value }"
          >
            <input
              v-model="form.embedding_mode"
              type="radio"
              :value="mode.value"
              :disabled="isEdit"
            />
            <span class="kb-mode__icon">
              <el-icon>
                <Files v-if="mode.value === 'dense'" />
                <Cpu v-else />
              </el-icon>
            </span>
            <span class="kb-mode__copy">
              <strong>{{ mode.label }}</strong>
              <small>{{ mode.description }}</small>
            </span>
            <span class="kb-mode__radio" />
          </label>
        </div>
        <p v-if="isEdit" class="kb-hint">
          检索模式与切块参数在创建后不可修改，可在上传文档时为单篇文档覆盖。
        </p>
      </el-form-item>

      <div class="kb-chunk">
        <div class="kb-chunk__item">
          <span class="kb-chunk__label">默认切块大小</span>
          <el-input-number
            v-model="form.chunk_size"
            :min="100"
            :max="2000"
            :step="50"
            :disabled="isEdit"
            controls-position="right"
          />
          <small>字符数，建议 400 - 800</small>
        </div>
        <div class="kb-chunk__item">
          <span class="kb-chunk__label">默认重叠大小</span>
          <el-input-number
            v-model="form.chunk_overlap"
            :min="0"
            :max="1000"
            :step="10"
            :disabled="isEdit"
            controls-position="right"
          />
          <small>需小于切块大小，建议 10% - 20%</small>
        </div>
      </div>
    </el-form>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">
        {{ isEdit ? '保存修改' : '创建知识库' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.kb-modes {
  display: flex;
  flex-direction: column;
  width: 100%;
  gap: 9px;
}

.kb-mode {
  position: relative;
  display: flex;
  align-items: flex-start;
  padding: 13px 15px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
  gap: 11px;
}

.kb-mode:hover {
  border-color: rgba(91, 91, 214, 0.3);
}

.kb-mode.is-active {
  background: #f8f7ff;
  border-color: rgba(91, 91, 214, 0.45);
  box-shadow: 0 0 0 3px rgba(91, 91, 214, 0.06);
}

.kb-modes.is-disabled .kb-mode {
  cursor: not-allowed;
  opacity: 0.7;
}

.kb-mode input {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.kb-mode__icon {
  display: grid;
  flex: none;
  width: 34px;
  height: 34px;
  color: var(--atf-brand);
  font-size: 16px;
  background: var(--atf-brand-soft);
  border-radius: 10px;
  place-items: center;
}

.kb-mode__copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.kb-mode__copy strong {
  font-size: 12.5px;
  font-weight: 700;
}

.kb-mode__copy small {
  color: var(--atf-muted);
  font-size: 10.5px;
  line-height: 1.5;
}

.kb-mode__radio {
  flex: none;
  width: 15px;
  height: 15px;
  margin-top: 2px;
  border: 1.5px solid #c9cbdc;
  border-radius: 50%;
  transition: all 0.2s ease;
}

.kb-mode.is-active .kb-mode__radio {
  background: var(--atf-brand);
  border-color: var(--atf-brand);
  box-shadow: inset 0 0 0 3px #fff;
}

.kb-hint {
  margin-top: 7px;
  color: var(--atf-muted);
  font-size: 10.5px;
  line-height: 1.5;
}

.kb-chunk {
  display: flex;
  gap: 14px;
}

.kb-chunk__item {
  display: flex;
  flex: 1;
  flex-direction: column;
  padding: 13px 14px;
  background: #faf9ff;
  border: 1px solid var(--atf-line);
  border-radius: 12px;
  gap: 6px;
}

.kb-chunk__label {
  color: var(--atf-ink-soft);
  font-size: 11.5px;
  font-weight: 650;
}

.kb-chunk__item :deep(.el-input-number) {
  width: 100%;
}

.kb-chunk__item small {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.kb-public {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  padding: 13px 15px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 13px;
  gap: 16px;
}

.kb-public.is-active {
  background: linear-gradient(120deg, #f4f3ff, #f0fbf8);
  border-color: rgba(91, 91, 214, 0.38);
}

.kb-public__copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kb-public__copy strong {
  font-size: 12.5px;
  font-weight: 700;
}

.kb-public__copy small {
  color: var(--atf-muted);
  font-size: 10.5px;
  line-height: 1.5;
}

@media (max-width: 600px) {
  .kb-chunk {
    flex-direction: column;
  }
}
</style>
