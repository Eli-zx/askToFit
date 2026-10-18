<script setup>
import BrandMark from '@/components/BrandMark.vue'
</script>

<template>
  <div class="auth">
    <div class="auth__aurora" />

    <aside class="auth__story">
      <div class="auth__story-top">
        <BrandMark light :size="40" />
        <span class="auth__badge">Retrieval-Augmented Commerce</span>
      </div>

      <div class="auth__story-copy">
        <h1>
          把每一次售前咨询，
          <br />
          变成<span>更懂客户</span>的对话。
        </h1>
        <p>
          基于电商服装知识库的智能导购。政策、物流、发票、尺码，
          一个对话入口全部答清楚，并且每一句都有出处。
        </p>
      </div>

      <div class="auth__mock" aria-hidden="true">
        <div class="auth__mock-card auth__mock-card--user">
          满 199 元包邮吗？新疆怎么算？
        </div>
        <div class="auth__mock-card auth__mock-card--bot">
          <span class="auth__mock-dot" />
          满 199 元包邮；新疆、西藏、内蒙古部分地区另加 15 元偏远运费。
          <small>引用《物流说明》· 运费标准</small>
        </div>
        <div class="auth__mock-pill">
          <i />
          检索增强生成 · 实时引用
        </div>
      </div>

      <footer class="auth__story-footer">
        <div>
          <strong>5+</strong>
          <span>业务知识域</span>
        </div>
        <div>
          <strong>SSE</strong>
          <span>流式回答</span>
        </div>
        <div>
          <strong>Hybrid</strong>
          <span>混合检索</span>
        </div>
      </footer>
    </aside>

    <section class="auth__panel">
      <div class="auth__mobile-brand">
        <BrandMark :size="34" />
      </div>
      <div class="auth__form-wrap">
        <slot />
      </div>
      <p class="auth__legal">© 2026 AskToFit · 企业级售前知识问答</p>
    </section>
  </div>
</template>

<style scoped>
.auth {
  display: grid;
  grid-template-columns: minmax(420px, 1.05fr) minmax(420px, 0.95fr);
  min-height: 100vh;
  overflow: hidden;
  background: #fbfaff;
}

.auth__aurora {
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 82% 12%, rgba(139, 124, 246, 0.14), transparent 34%),
    radial-gradient(circle at 58% 88%, rgba(45, 212, 191, 0.12), transparent 32%);
}

.auth__story {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  padding: 44px 56px 40px;
  overflow: hidden;
  color: #fff;
  background:
    linear-gradient(150deg, rgba(255, 255, 255, 0.06), transparent 40%),
    linear-gradient(145deg, #24245c 0%, #3a3487 44%, #4d43ad 76%, #356f8a 130%);
}

.auth__story::before,
.auth__story::after {
  position: absolute;
  content: '';
  border-radius: 50%;
  filter: blur(8px);
}

.auth__story::before {
  top: -140px;
  right: -120px;
  width: 380px;
  height: 380px;
  background: radial-gradient(circle, rgba(139, 124, 246, 0.55), transparent 68%);
  animation: atf-float 16s ease-in-out infinite;
}

.auth__story::after {
  bottom: -170px;
  left: -110px;
  width: 420px;
  height: 420px;
  background: radial-gradient(circle, rgba(45, 212, 191, 0.34), transparent 68%);
  animation: atf-float 20s ease-in-out infinite reverse;
}

.auth__story-top {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.auth__badge {
  padding: 7px 12px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 9.5px;
  font-weight: 650;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 999px;
}

.auth__story-copy {
  position: relative;
  z-index: 1;
  margin-top: clamp(58px, 9vh, 108px);
}

.auth__story-copy h1 {
  font-size: clamp(32px, 3.2vw, 47px);
  font-weight: 800;
  letter-spacing: -0.035em;
  line-height: 1.22;
}

.auth__story-copy h1 span {
  color: #b9f6e9;
}

.auth__story-copy p {
  max-width: 430px;
  margin-top: 22px;
  color: rgba(255, 255, 255, 0.62);
  font-size: 14px;
  line-height: 1.9;
}

.auth__mock {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  max-width: 430px;
  margin-top: 44px;
  gap: 12px;
}

.auth__mock-card {
  padding: 15px 18px;
  font-size: 12.5px;
  line-height: 1.7;
  border-radius: 16px;
  animation: atf-rise 0.7s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.auth__mock-card--user {
  align-self: flex-end;
  color: #fff;
  background: rgba(255, 255, 255, 0.14);
  border: 1px solid rgba(255, 255, 255, 0.13);
  border-bottom-right-radius: 5px;
  backdrop-filter: blur(10px);
  animation-delay: 0.12s;
}

.auth__mock-card--bot {
  align-self: flex-start;
  max-width: 92%;
  color: rgba(29, 30, 51, 0.86);
  background: rgba(255, 255, 255, 0.94);
  border-bottom-left-radius: 5px;
  animation-delay: 0.28s;
}

.auth__mock-card--bot small {
  display: block;
  margin-top: 7px;
  color: #7a7fa4;
  font-size: 10.5px;
}

.auth__mock-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 6px;
  background: #2dd4bf;
  border-radius: 50%;
  box-shadow: 0 0 0 3px rgba(45, 212, 191, 0.16);
}

.auth__mock-pill {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  padding: 7px 13px;
  margin-top: 4px;
  color: rgba(255, 255, 255, 0.62);
  font-size: 10.5px;
  letter-spacing: 0.04em;
  background: rgba(10, 12, 34, 0.22);
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 999px;
  gap: 8px;
}

.auth__mock-pill i {
  width: 5px;
  height: 5px;
  background: #8fe8d9;
  border-radius: 50%;
  animation: atf-pulse 2s ease-in-out infinite;
}

.auth__story-footer {
  position: relative;
  z-index: 1;
  display: flex;
  margin-top: auto;
  padding-top: 36px;
  gap: clamp(26px, 4vw, 54px);
}

.auth__story-footer div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.auth__story-footer strong {
  font-size: 17px;
  font-weight: 750;
  letter-spacing: -0.02em;
}

.auth__story-footer span {
  color: rgba(255, 255, 255, 0.46);
  font-size: 10.5px;
  letter-spacing: 0.08em;
}

.auth__panel {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 40px 28px;
}

.auth__form-wrap {
  width: min(400px, 100%);
  animation: atf-rise 0.6s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.auth__mobile-brand {
  display: none;
  width: min(400px, 100%);
  margin-bottom: 30px;
}

.auth__legal {
  margin-top: 40px;
  color: #a3a6bd;
  font-size: 10.5px;
  letter-spacing: 0.03em;
}

@media (max-width: 980px) {
  .auth {
    grid-template-columns: 1fr;
  }

  .auth__story {
    display: none;
  }

  .auth__panel {
    min-height: 100vh;
    padding: 38px 24px 24px;
  }

  .auth__mobile-brand {
    display: block;
  }
}
</style>
