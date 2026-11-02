<script setup>
import BrandMark from '@/components/BrandMark.vue'
</script>

<template>
  <div class="notfound">
    <BrandMark :size="40" />
    <div class="notfound__code">404</div>
    <h1>这条路径没有对应的页面</h1>
    <p>可能是链接已失效，或者地址输入有误。</p>
    <RouterLink to="/chat">
      <el-button type="primary" size="large">返回智能对话</el-button>
    </RouterLink>
  </div>
</template>

<style scoped>
.notfound {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  text-align: center;
}

.notfound__code {
  margin-top: 28px;
  color: var(--atf-brand);
  font-family: var(--atf-font-display);
  font-size: clamp(90px, 18vw, 170px);
  font-style: italic;
  font-weight: 600;
  letter-spacing: -0.05em;
  line-height: 1;
}

.notfound h1 {
  margin-top: 8px;
  font-family: var(--atf-font-display);
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.015em;
}

.notfound p {
  margin-top: 8px;
  color: var(--atf-muted);
  font-size: 12.5px;
}

.notfound a {
  margin-top: 24px;
}
</style>
