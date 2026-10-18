<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Key, Lock, User } from '@element-plus/icons-vue'

import { register } from '@/api/auth'
import AuthLayout from '@/components/AuthLayout.vue'
import CaptchaField from '@/components/CaptchaField.vue'
import { toErrorMessage } from '@/utils/errors'

const router = useRouter()

const formRef = ref()
const captchaRef = ref()
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
  confirm_password: '',
  captcha_code: '',
})
const captchaId = ref('')

function validateConfirm(rule, value, callback) {
  if (!value) {
    callback(new Error('请再次输入密码'))
  } else if (value !== form.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const rules = {
  username: [
    { required: true, message: '请输入账号名称', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_]{3,32}$/, message: '3-32 位字母、数字或下划线', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度为 6-64 位', trigger: 'blur' },
  ],
  confirm_password: [{ validator: validateConfirm, trigger: 'blur' }],
  captcha_code: [{ required: true, message: '请输入验证码', trigger: 'blur' }],
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
    await register({
      username: form.username.trim(),
      password: form.password,
      confirm_password: form.confirm_password,
      captcha_id: captchaId.value,
      captcha_code: form.captcha_code.trim(),
    })
    ElMessage.success('注册成功，请登录')
    await router.push({ name: 'login', query: { username: form.username.trim().toLowerCase() } })
  } catch (error) {
    ElMessage.error(toErrorMessage(error, '注册失败，请稍后重试'))
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
        <span class="atf-eyebrow">Create account</span>
        <h2>创建你的工作台</h2>
        <p>注册后即可创建知识库、上传文档，并开始多轮售前问答。</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="submit"
      >
        <el-form-item label="账号名称" prop="username">
          <el-input
            v-model="form.username"
            maxlength="32"
            placeholder="3-32 位字母、数字或下划线"
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
            placeholder="至少 6 位字符"
            autocomplete="new-password"
          >
            <template #prefix>
              <el-icon><Lock /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item label="确认密码" prop="confirm_password">
          <el-input
            v-model="form.confirm_password"
            type="password"
            show-password
            placeholder="请再次输入密码"
            autocomplete="new-password"
          >
            <template #prefix>
              <el-icon><Key /></el-icon>
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
          注册账号
        </el-button>
      </el-form>

      <p class="auth-form__switch">
        已有账号？
        <RouterLink to="/login">返回登录</RouterLink>
      </p>
    </div>
  </AuthLayout>
</template>

<style scoped>
.auth-form__head {
  margin-bottom: 24px;
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
  margin-bottom: 16px;
}

.auth-form :deep(.el-form-item__label) {
  padding-bottom: 6px;
  color: var(--atf-ink-soft);
  font-size: 12.5px;
  font-weight: 650;
}

.auth-form :deep(.el-input__wrapper) {
  height: 44px;
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
  margin-top: 22px;
  color: var(--atf-muted);
  font-size: 13px;
  text-align: center;
}

.auth-form__switch a {
  font-weight: 700;
}
</style>
