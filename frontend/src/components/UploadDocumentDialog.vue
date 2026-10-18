<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'

import { getParserOptions, uploadDocument } from '@/api/knowledge'
import {
  EMBEDDING_MODES,
  FILE_ACCEPT,
  MAX_UPLOAD_MB,
  MINERU_EXTENSIONS,
} from '@/utils/constants'
import { toErrorMessage } from '@/utils/errors'
import { formatFileSize } from '@/utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  knowledgeBase: { type: Object, default: null },
})

const emit = defineEmits(['update:modelValue', 'uploaded'])

const uploadRef = ref()
const selectedFile = ref(null)
const uploading = ref(false)
const progress = ref(0)
const parserOptions = ref(null)
const parserLoading = ref(false)

const form = reactive({
  chunk_size: 500,
  chunk_overlap: 80,
  embedding_mode: 'hybrid',
  force_new: false,
  parser: 'auto',
})

const allowedExtensions = computed(() =>
  FILE_ACCEPT.split(',').map((item) => item.replace('.', '')),
)

const mineruOption = computed(() => parserOptions.value?.mineru)
const mineruAvailable = computed(() => Boolean(mineruOption.value?.available))
const mineruApplicable = computed(() => {
  const extension = selectedFile.value?.name.split('.').pop()?.toLowerCase() || ''
  return MINERU_EXTENSIONS.includes(extension)
})
const mineruReady = computed(() => mineruAvailable.value && mineruApplicable.value)
const mineruHint = computed(() => {
  if (parserLoading.value) return '正在检测 MinerU 是否可用…'
  if (!parserOptions.value) return '暂时无法检测 MinerU 状态，将使用标准解析'
  if (!mineruAvailable.value) {
    return mineruOption.value?.reason || 'MinerU 未就绪，将使用标准解析'
  }
  if (selectedFile.value && !mineruApplicable.value) {
    return 'Markdown 等文本格式无需深度解析，将使用标准解析'
  }
  return '复杂表格、扫描件、多栏排版识别更准确，解析耗时更长'
})

async function loadParserOptions() {
  parserLoading.value = true
  try {
    parserOptions.value = await getParserOptions()
  } catch {
    parserOptions.value = null
  } finally {
    parserLoading.value = false
  }
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) return
    selectedFile.value = null
    uploading.value = false
    progress.value = 0
    form.chunk_size = props.knowledgeBase?.chunk_size ?? 500
    form.chunk_overlap = props.knowledgeBase?.chunk_overlap ?? 80
    form.embedding_mode = props.knowledgeBase?.embedding_mode ?? 'hybrid'
    form.force_new = false
    form.parser = 'auto'
    uploadRef.value?.clearFiles()
    loadParserOptions()
  },
)

function close() {
  if (!uploading.value) emit('update:modelValue', false)
}

function validateFile(raw) {
  if (!raw) return false
  const extension = raw.name.split('.').pop()?.toLowerCase() || ''
  if (!allowedExtensions.value.includes(extension)) {
    ElMessage.error(`不支持的文件类型：${extension || '未知'}`)
    return false
  }
  if (raw.size > MAX_UPLOAD_MB * 1024 * 1024) {
    ElMessage.error(`文件不能超过 ${MAX_UPLOAD_MB}MB`)
    return false
  }
  return true
}

function handleChange(uploadFile) {
  const raw = uploadFile.raw
  if (!validateFile(raw)) {
    uploadRef.value?.clearFiles()
    selectedFile.value = null
    return
  }
  selectedFile.value = raw
  if (!mineruApplicable.value) form.parser = 'auto'
}

function handleRemove() {
  selectedFile.value = null
}

function handleExceed() {
  ElMessage.warning('一次只能上传一个文档，请先移除已选文件')
}

async function submit() {
  if (uploading.value) return
  if (!selectedFile.value) {
    ElMessage.warning('请先选择要上传的文档')
    return
  }
  if (!Number.isFinite(form.chunk_size) || form.chunk_size < 100 || form.chunk_size > 2000) {
    ElMessage.warning('切块大小需在 100 - 2000 之间')
    return
  }
  if (form.chunk_overlap < 0 || form.chunk_overlap >= form.chunk_size) {
    ElMessage.warning('重叠大小需大于等于 0 且小于切块大小')
    return
  }

  uploading.value = true
  progress.value = 0
  try {
    const document = await uploadDocument(
      props.knowledgeBase.id,
      selectedFile.value,
      {
        chunkSize: form.chunk_size,
        chunkOverlap: form.chunk_overlap,
        embeddingMode: form.embedding_mode,
        forceNew: form.force_new,
        parser: form.parser,
      },
      (value) => {
        progress.value = value
      },
    )
    ElMessage.success('上传成功，后台正在解析；内容重复或高度相似时会自动更新，不会重复入库')
    emit('uploaded', document)
    emit('update:modelValue', false)
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '文档上传失败'))
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="上传并解析文档"
    width="620px"
    top="6vh"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="upload">
      <div class="upload__target">
        <span>目标知识库</span>
        <strong>{{ knowledgeBase?.name || '—' }}</strong>
      </div>

      <el-upload
        ref="uploadRef"
        class="upload__drop"
        drag
        :auto-upload="false"
        :limit="1"
        :accept="FILE_ACCEPT"
        :on-change="handleChange"
        :on-remove="handleRemove"
        :on-exceed="handleExceed"
      >
        <el-icon class="upload__icon"><UploadFilled /></el-icon>
        <div class="upload__title">拖拽文件到这里，或点击选择</div>
        <div class="upload__hint">
          支持 PDF / PPT / Word（DOC / DOCX）/ Markdown / PNG / JPG，单个文件不超过
          {{ MAX_UPLOAD_MB }}MB
        </div>
      </el-upload>

      <div v-if="selectedFile" class="upload__file">
        <span class="upload__file-name">{{ selectedFile.name }}</span>
        <span class="upload__file-size">{{ formatFileSize(selectedFile.size) }}</span>
      </div>

      <div class="upload__section">
        <div class="upload__section-head">
          <span>解析参数</span>
          <small>仅对本次上传生效，默认继承知识库配置</small>
        </div>

        <div class="upload__grid">
          <label class="upload__field">
            <span>切块大小</span>
            <el-input-number
              v-model="form.chunk_size"
              :min="100"
              :max="2000"
              :step="50"
              controls-position="right"
            />
          </label>
          <label class="upload__field">
            <span>重叠大小</span>
            <el-input-number
              v-model="form.chunk_overlap"
              :min="0"
              :max="1000"
              :step="10"
              controls-position="right"
            />
          </label>
        </div>

        <div class="upload__modes">
          <label
            v-for="mode in EMBEDDING_MODES"
            :key="mode.value"
            class="upload__mode"
            :class="{ 'is-active': form.embedding_mode === mode.value }"
          >
            <input v-model="form.embedding_mode" type="radio" :value="mode.value" />
            <span>
              <strong>{{ mode.label }}</strong>
              <small>{{ mode.description }}</small>
            </span>
          </label>
        </div>
      </div>

      <div class="upload__force">
        <div class="upload__force-text">
          <strong>强制作为新文档入库</strong>
          <small>
            默认开启正文判重：只改标点或格式不会重复入库，内容更新会替换旧版本；
            仅在确需保留同名独立文档时开启
          </small>
        </div>
        <el-switch v-model="form.force_new" />
      </div>

      <div class="upload__force upload__parser">
        <div class="upload__force-text">
          <strong>深度解析（MinerU）</strong>
          <small :class="{ 'is-warning': parserOptions && !mineruAvailable }">
            {{ mineruHint }}
          </small>
        </div>
        <el-switch
          v-model="form.parser"
          active-value="mineru"
          inactive-value="auto"
          :disabled="!mineruReady"
          :loading="parserLoading"
        />
      </div>

      <div class="upload__pipeline">
        <span>解析</span>
        <i />
        <span>切块</span>
        <i />
        <span>向量化</span>
        <i />
        <span>写入 Milvus</span>
      </div>

      <el-progress
        v-if="uploading"
        class="upload__progress"
        :percentage="progress"
        :stroke-width="6"
        status="active"
      />
    </div>

    <template #footer>
      <el-button :disabled="uploading" @click="close">取消</el-button>
      <el-button type="primary" :loading="uploading" @click="submit">
        上传并开始解析
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.upload__target {
  display: flex;
  align-items: center;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: linear-gradient(120deg, #f4f3ff, #f0fbf8);
  border: 1px solid var(--atf-line);
  border-radius: 11px;
  gap: 9px;
}

.upload__target span {
  color: var(--atf-muted);
  font-size: 10.5px;
}

.upload__target strong {
  color: var(--atf-brand-deep);
  font-size: 12px;
}

.upload__drop :deep(.el-upload-dragger) {
  padding: 26px 20px;
  background: #fbfbff;
  border: 1.5px dashed rgba(91, 91, 214, 0.24);
  border-radius: 14px;
  transition: all 0.2s ease;
}

.upload__drop :deep(.el-upload-dragger:hover) {
  background: #f7f6ff;
  border-color: rgba(91, 91, 214, 0.46);
}

.upload__icon {
  color: var(--atf-brand);
  font-size: 34px;
}

.upload__title {
  margin-top: 7px;
  color: var(--atf-ink-soft);
  font-size: 12.5px;
  font-weight: 650;
}

.upload__hint {
  margin-top: 4px;
  color: var(--atf-muted);
  font-size: 10px;
}

.upload__file {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  margin-top: 9px;
  background: var(--atf-brand-soft);
  border-radius: 9px;
}

.upload__file-name {
  overflow: hidden;
  color: var(--atf-brand-deep);
  font-size: 11px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload__file-size {
  flex: none;
  color: var(--atf-muted);
  font-size: 10px;
}

.upload__section {
  margin-top: 18px;
}

.upload__section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 9px;
}

.upload__section-head span {
  font-size: 12.5px;
  font-weight: 700;
}

.upload__section-head small {
  color: var(--atf-muted);
  font-size: 9.5px;
}

.upload__grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.upload__field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.upload__field > span {
  color: var(--atf-ink-soft);
  font-size: 10.5px;
  font-weight: 600;
}

.upload__field :deep(.el-input-number) {
  width: 100%;
}

.upload__modes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  margin-top: 11px;
  gap: 9px;
}

.upload__mode {
  position: relative;
  display: flex;
  padding: 10px 12px;
  background: #fff;
  border: 1px solid var(--atf-line);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.upload__mode.is-active {
  background: #f8f7ff;
  border-color: rgba(91, 91, 214, 0.4);
}

.upload__mode input {
  position: absolute;
  opacity: 0;
}

.upload__mode span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.upload__mode strong {
  font-size: 11px;
  font-weight: 650;
}

.upload__mode small {
  color: var(--atf-muted);
  font-size: 9.5px;
  line-height: 1.45;
}

.upload__force {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 13px;
  margin-top: 14px;
  background: #faf9ff;
  border: 1px solid var(--atf-line);
  border-radius: 10px;
  gap: 12px;
}

.upload__force-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.upload__force-text strong {
  color: var(--atf-ink-soft);
  font-size: 11px;
  font-weight: 650;
}

.upload__force-text small {
  color: var(--atf-muted);
  font-size: 9.5px;
  line-height: 1.5;
}

.upload__force-text small.is-warning {
  color: #c2703d;
}

.upload__parser {
  background: #fffaf3;
}

.upload__pipeline {
  display: flex;
  align-items: center;
  padding: 11px 14px;
  margin-top: 16px;
  color: #8b8eaa;
  font-size: 9.5px;
  background: #faf9ff;
  border: 1px dashed var(--atf-line-strong);
  border-radius: 10px;
  justify-content: space-between;
}

.upload__pipeline i {
  flex: 1;
  height: 1px;
  margin: 0 8px;
  background: linear-gradient(90deg, rgba(91, 91, 214, 0.2), rgba(45, 212, 191, 0.3));
}

.upload__progress {
  margin-top: 14px;
}

@media (max-width: 600px) {
  .upload__grid,
  .upload__modes {
    grid-template-columns: 1fr;
  }
}
</style>
