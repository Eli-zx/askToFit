<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'

import { login } from '@/api/auth'
import AuthLayout from '@/components/AuthLayout.vue'
import CaptchaField from '@/components/CaptchaField.vue'
import { useAuthStore } from '@/stores/auth'
import { toErrorMessage } from '@/utils/errors'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref()
const captchaRef = ref()
const loading = ref(false)

const form = reactive({
  username: String(route.query.username || ''),
  password: '',
  captcha_code: '',
})
const captchaId = ref('')

const rules = {
  username: [
    { required: true, message: '请输入账号', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_]{3,32}$/, message: '3-32 位字母、数字或下划线', trigger: 'blur' },
  ],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  captcha_code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
}

function resolveRedirect() {
  const target = String(route.query.redirect || '')
  return target.startsWith('/') && !target.startsWith('//') ? target : '/chat'
}

async function submit() {
  if (loading.value) return
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    const session = await login({
      username: form.username.trim(),
      password: form.password,
      captcha_id: captchaId.value,
      captcha_code: form.captcha_code.trim(),
    })
    auth.applySession(session)
    ElMessage.success(`欢迎回来，${session.user.nickname || session.user.username}`)
    await router.replace(resolveRedirect())
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '登录失败，请检查账号密码'))
    captchaRef.value?.refresh()
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthLayout>
    <div class="auth-form">
      <div class="auth-form__head">
        <span class="atf-eyebrow">Welcome back</span>
        <h2>登录 AskToFit</h2>
        <p>继续你的售前咨询工作台，历史会话与知识库已为你保留。</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="submit"
      >
        <el-form-item label="账号" prop="username">
          <el-input
            v-model="form.username"
            maxlength="32"
            placeholder="请输入账号名称"
            autocomplete="username"
          >
            <template #prefix>
              <el-icon><User /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="请输入登录密码"
            autocomplete="current-password"
            @keyup.enter="submit"
          >
            <template #prefix>
              <el-icon><Lock /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item label="图形验证码" prop="captcha_code">
          <CaptchaField
            ref="captchaRef"
            v-model="form.captcha_code"
            @change="captchaId = $event"
          />
        </el-form-item>

        <el-button
          class="auth-form__submit"
          type="primary"
          native-type="submit"
          :loading="loading"
        >
          登录并进入工作台
        </el-button>
      </el-form>

      <p class="auth-form__switch">
        还没有账号？
        <RouterLink to="/register">立即注册</RouterLink>
      </p>
    </div>
  </AuthLayout>
</template>

<style scoped>
.auth-form__head {
  margin-bottom: 28px;
}

.auth-form__head h2 {
  margin-top: 16px;
  font-size: 30px;
  font-weight: 800;
  letter-spacing: -0.03em;
}

.auth-form__head p {
  margin-top: 10px;
  color: var(--atf-muted);
  font-size: 13px;
  line-height: 1.7;
}

.auth-form :deep(.el-form-item) {
  margin-bottom: 20px;
}

.auth-form :deep(.el-form-item__label) {
  padding-bottom: 7px;
  color: var(--atf-ink-soft);
  font-size: 12.5px;
  font-weight: 650;
}

.auth-form :deep(.el-input__wrapper) {
  height: 46px;
  padding: 0 14px;
  background: #fff;
  border: 1px solid var(--atf-line);
  box-shadow: none;
}

.auth-form :deep(.el-input__wrapper.is-focus) {
  border-color: rgba(91, 91, 214, 0.52);
  box-shadow: 0 0 0 4px rgba(91, 91, 214, 0.08);
}

.auth-form :deep(.el-input__prefix) {
  color: #b0b2c7;
}

.auth-form__submit {
  width: 100%;
  height: 48px;
  margin-top: 4px;
  font-size: 14px;
  letter-spacing: 0.02em;
  border-radius: 12px;
}

.auth-form__switch {
  margin-top: 24px;
  color: var(--atf-muted);
  font-size: 13px;
  text-align: center;
}

.auth-form__switch a {
  font-weight: 700;
}
</style>
