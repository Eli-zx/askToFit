<script setup>
import BrandMark from '@/components/BrandMark.vue'
import ThemeToggle from '@/components/ThemeToggle.vue'
</script>

<template>
  <div class="auth">
    <aside class="auth__story">
      <div class="auth__story-top">
        <BrandMark light :size="40" />
        <span class="auth__badge">Private Client Advisory</span>
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
          回答之前，先查资料
        </div>
      </div>

      <footer class="auth__story-footer">
        <div>
          <strong>5+</strong>
          <span>业务知识域</span>
        </div>
        <div>
          <strong>24H</strong>
          <span>在线应答</span>
        </div>
        <div>
          <strong>逐条</strong>
          <span>引用可溯</span>
        </div>
      </footer>
    </aside>

    <section class="auth__panel">
      <div class="auth__theme">
        <ThemeToggle show-label />
      </div>
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
  background: var(--atf-bg);
}

/* 左栏做成杂志封面：墨色底、衬线大标题、细线分隔 */
.auth__story {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 46px 56px 40px;
  overflow: hidden;
  color: var(--atf-story-ink);
  background: var(--atf-story-bg);
}

.auth__story::before {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 1px;
  content: '';
  background: var(--atf-story-line);
}

.auth__story-top {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--atf-story-line);
  gap: 16px;
}

.auth__badge {
  padding: 5px 8px;
  color: var(--atf-story-soft);
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  border: 1px solid var(--atf-story-line);
  border-radius: 1px;
}

.auth__story-copy {
  position: relative;
  z-index: 1;
  margin-top: clamp(58px, 9vh, 108px);
}

.auth__story-copy h1 {
  font-family: var(--atf-font-display);
  font-size: clamp(33px, 3.4vw, 50px);
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.24;
}

.auth__story-copy h1 span {
  color: var(--atf-story-accent);
}

.auth__story-copy p {
  max-width: 430px;
  margin-top: 24px;
  color: var(--atf-story-soft);
  font-size: 13.5px;
  line-height: 1.95;
}

.auth__mock {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  max-width: 440px;
  margin-top: 44px;
  gap: 11px;
}

.auth__mock-card {
  padding: 14px 17px;
  font-size: 12.5px;
  line-height: 1.75;
  border-radius: 1px;
  animation: atf-rise 0.7s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.auth__mock-card--user {
  align-self: flex-end;
  color: var(--atf-story-ink);
  background: var(--atf-story-card);
  border: 1px solid var(--atf-story-card-line);
  animation-delay: 0.12s;
}

.auth__mock-card--bot {
  align-self: flex-start;
  max-width: 92%;
  color: var(--atf-story-bubble-ink);
  background: var(--atf-story-bubble-bg);
  animation-delay: 0.28s;
}

.auth__mock-card--bot small {
  display: block;
  margin-top: 8px;
  padding-top: 7px;
  color: var(--atf-muted);
  font-size: 10px;
  border-top: 1px solid var(--atf-story-bubble-line);
}

.auth__mock-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  margin-right: 6px;
  background: var(--atf-brand);
  border-radius: 50%;
}

.auth__mock-pill {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  padding: 5px 0;
  margin-top: 4px;
  color: var(--atf-story-soft);
  font-family: var(--atf-font-mono);
  font-size: 8.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  gap: 8px;
}

.auth__mock-pill i {
  width: 5px;
  height: 5px;
  background: var(--atf-story-dot);
  border-radius: 50%;
  animation: atf-pulse 2s ease-in-out infinite;
}

.auth__story-footer {
  position: relative;
  z-index: 1;
  display: flex;
  margin-top: auto;
  padding-top: 30px;
  border-top: 1px solid var(--atf-story-line);
  gap: clamp(26px, 4vw, 54px);
}

.auth__story-footer div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.auth__story-footer strong {
  font-family: var(--atf-font-display);
  font-size: 19px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.auth__story-footer span {
  color: var(--atf-story-faint);
  font-size: 9.5px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}

.auth__panel {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 40px 28px;
  background: var(--atf-surface);
}

.auth__theme {
  position: absolute;
  top: 20px;
  right: 26px;
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
  color: var(--atf-muted);
  font-family: var(--atf-font-mono);
  font-size: 9px;
  letter-spacing: 0.06em;
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
