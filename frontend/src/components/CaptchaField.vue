<script setup>
import { onMounted, ref } from 'vue'
import { RefreshRight } from '@element-plus/icons-vue'

import { fetchCaptcha } from '@/api/auth'
import { toErrorMessage } from '@/utils/errors'

defineProps({
  modelValue: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'change'])

const image = ref('')
const captchaId = ref('')
const loading = ref(false)
const error = ref('')

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchCaptcha()
    image.value = data.image
    captchaId.value = data.captcha_id
    emit('change', data.captcha_id)
    emit('update:modelValue', '')
  } catch (err) {
    error.value = toErrorMessage(err, '验证码加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(refresh)

defineExpose({ refresh, captchaId })
</script>

<template>
  <div class="captcha">
    <el-input
      :model-value="modelValue"
      maxlength="6"
      placeholder="验证码"
      autocomplete="off"
      @update:model-value="emit('update:modelValue', $event)"
    >
      <template #prefix>
        <el-icon><RefreshRight /></el-icon>
      </template>
    </el-input>

    <button
      class="captcha__image"
      type="button"
      :disabled="loading"
      title="点击刷新验证码"
      @click="refresh"
    >
      <img v-if="image" :src="image" alt="图形验证码" />
      <span v-else>{{ error || '点击加载' }}</span>
      <span class="captcha__refresh">
        <el-icon><RefreshRight /></el-icon>
      </span>
    </button>
  </div>
</template>

<style scoped>
.captcha {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 10px;
}

.captcha :deep(.el-input) {
  flex: 1;
}

.captcha :deep(.el-input__wrapper) {
  height: 46px;
}

.captcha__image {
  position: relative;
  display: grid;
  flex: none;
  width: 132px;
  height: 46px;
  padding: 0;
  overflow: hidden;
  color: var(--atf-muted);
  font-size: 12px;
  background: #f4f4fa;
  border: 1px solid var(--atf-line);
  border-radius: 10px;
  cursor: pointer;
  place-items: center;
}

.captcha__image img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.captcha__refresh {
  position: absolute;
  right: 4px;
  bottom: 4px;
  display: grid;
  width: 18px;
  height: 18px;
  color: #fff;
  font-size: 11px;
  background: rgba(23, 24, 47, 0.46);
  border-radius: 50%;
  opacity: 0;
  transition: opacity 0.2s ease;
  place-items: center;
}

.captcha__image:hover .captcha__refresh {
  opacity: 1;
}
</style>
