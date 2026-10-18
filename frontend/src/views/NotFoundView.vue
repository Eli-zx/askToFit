<script setup>
import BrandMark from '@/components/BrandMark.vue'
</script>

<template>
  <div class="notfound">
    <div class="atf-aurora" />
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
  margin-top: 32px;
  color: transparent;
  font-size: clamp(90px, 18vw, 170px);
  font-weight: 900;
  letter-spacing: -0.07em;
  line-height: 1;
  background: linear-gradient(120deg, #5b5bd6, #8b7cf6 55%, #2dd4bf);
  background-clip: text;
  -webkit-background-clip: text;
}

.notfound h1 {
  margin-top: 8px;
  font-size: 22px;
  font-weight: 750;
  letter-spacing: -0.02em;
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
