/**
 * 新增功能端到端验收：会话归档 / 恢复、知识缺口运营台。
 *
 * 使用独立临时账号与数据，直接注入 JWT 跳过验证码登录；
 * 运行前需启动后端与前端（E2E_BASE_URL 默认 http://127.0.0.1:4173）。
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright-core'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND_DIR = path.resolve(__dirname, '..')
const PROJECT_DIR = path.resolve(FRONTEND_DIR, '..')
const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:4173'

const stamp = Date.now().toString(36)
const username = `e2e_feat_${stamp}`
const question = '请问支持定制婚纱吗？'

function findBrowser() {
  if (process.env.CHROME_PATH && fs.existsSync(process.env.CHROME_PATH)) {
    return process.env.CHROME_PATH
  }
  const candidates = [
    path.join(
      process.env.LOCALAPPDATA || '',
      'ms-playwright',
      'chromium-1243',
      'chrome-win64',
      'chrome.exe',
    ),
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  ]
  const found = candidates.find((item) => item && fs.existsSync(item))
  if (!found) throw new Error('未找到 Chromium/Chrome/Edge，可通过 CHROME_PATH 指定')
  return found
}

function runPython(script) {
  const output = execFileSync('uv', ['run', 'python', '-c', script], {
    cwd: PROJECT_DIR,
    encoding: 'utf8',
    timeout: 120000,
  })
  return output.trim().split(/\r?\n/).pop()
}

function setupData() {
  const script = `
import asyncio, json
from datetime import datetime
from app.core.security import create_access_token, hash_password
from app.db.session import dispose_engine, get_session_maker
from app.models.conversation import Conversation
from app.models.eval_run import EvalCase, EvalRun
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_gap import KnowledgeGap
from app.models.user import User

async def main():
    async with get_session_maker()() as session:
        user = User(
            username="${username}",
            password_hash=hash_password("AtfTest123!"),
            nickname="E2E 功能验收",
            is_admin=True,
        )
        session.add(user)
        await session.flush()
        kb = KnowledgeBase(owner_id=user.id, name="E2E 功能验收库", description="临时", document_count=0)
        session.add(kb)
        await session.flush()
        conversation = Conversation(
            user_id=user.id,
            title="归档验收会话",
            kb_ids=[kb.id],
            last_message="历史消息",
        )
        session.add(conversation)
        await session.flush()
        gap = KnowledgeGap(
            user_id=user.id,
            conversation_id=conversation.id,
            question="${question}",
            intent="product",
            intent_confidence=0.91,
            intent_source="bert",
            kb_ids=[kb.id],
            best_score=0.18,
            vector_score=0.18,
            rerank_score=None,
            retrieval_mode="vector",
            status="open",
        )
        session.add(gap)

        session.add(EvalRun(
            owner_id=user.id,
            dataset_name="qa_eval.jsonl",
            dataset_sha256="e2e-history",
            kb_id=kb.id,
            kb_name=kb.name,
            strategy="direct",
            top_k=10,
            sample_count=48,
            answerable_count=48,
            retrieval_metrics={
                "doc": {"hit_at_k": 0.8, "recall_at_k": 0.7, "mrr": 0.65, "ndcg_at_k": 0.7},
                "section": {"hit_at_k": 0.5, "recall_at_k": 0.45, "mrr": 0.4, "ndcg_at_k": 0.45},
            },
            generation_metrics={"faithfulness": 0.72},
            config_snapshot={"retrieval_k": 5},
            status="success",
        ))

        run = EvalRun(
            owner_id=user.id,
            dataset_name="qa_eval.jsonl",
            dataset_sha256="e2e-sha256",
            kb_id=kb.id,
            kb_name=kb.name,
            strategy="auto",
            top_k=10,
            judge_model="e2e-judge",
            ragas_version="0.3.0",
            sample_count=2,
            answerable_count=1,
            retrieval_metrics={
                "doc": {"hit_at_k": 1.0, "recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0},
                "section": {"hit_at_k": 0.5, "recall_at_k": 0.75, "mrr": 0.5, "ndcg_at_k": 0.6},
                "keyword_coverage": 1.0,
            },
            generation_metrics={
                "faithfulness": 0.86,
                "answer_relevancy": 0.8,
                "context_precision": 0.75,
                "context_recall": 0.9,
            },
            config_snapshot={"retrieval_k": 5},
            status="partial",
        )
        session.add(run)
        await session.flush()
        session.add_all([
            EvalCase(
                run_id=run.id,
                case_id="eval-e2e-1",
                question="满多少包邮？",
                ground_truth="满 99 元包邮。",
                answer="满 99 元包邮。",
                intent="logistics",
                tags=["single-hop"],
                expected_doc_names=["物流仓配与时效说明.md"],
                expected_sections=["运费标准"],
                retrieved=[{"doc_name": "物流仓配与时效说明.md", "section": "运费标准", "score": 0.9}],
                doc_metrics={"hit_at_k": 1.0, "recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0},
                section_metrics={"hit_at_k": 1.0, "recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0},
                generation_metrics={"faithfulness": 0.86, "answer_relevancy": 0.8},
                keyword_coverage=1.0,
            ),
            EvalCase(
                run_id=run.id,
                case_id="eval-e2e-2",
                question="知识库未收录的问题？",
                ground_truth="知识库未收录。",
                intent="other",
                tags=["unanswerable"],
                expected_doc_names=[],
                expected_sections=[],
                retrieved=[],
                doc_metrics={"hit_at_k": 0.0, "recall_at_k": 0.0, "mrr": 0.0, "ndcg_at_k": 0.0},
                section_metrics={"hit_at_k": 0.0, "recall_at_k": 0.0, "mrr": 0.0, "ndcg_at_k": 0.0},
                keyword_coverage=None,
                error="检索失败：模拟压测异常",
            ),
        ])
        await session.commit()
        token, _ = create_access_token(user.id, user.username)
        print(json.dumps({
            "token": token,
            "user_id": user.id,
            "conversation_id": conversation.id,
            "gap_id": gap.id,
            "run_id": run.id,
        }, ensure_ascii=False))
    await dispose_engine()

asyncio.run(main())
`
  return JSON.parse(runPython(script))
}

function cleanupData(userId) {
  const script = `
import asyncio
from sqlalchemy import delete, text
from app.db.session import dispose_engine, get_engine

async def main():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM eval_cases WHERE run_id IN (SELECT id FROM eval_runs WHERE owner_id = ${userId})"))
        await conn.execute(text("DELETE FROM eval_runs WHERE owner_id = ${userId}"))
        await conn.execute(text("DELETE FROM knowledge_gaps WHERE user_id = ${userId}"))
        await conn.execute(text("DELETE FROM users WHERE id = ${userId}"))
    await dispose_engine()

asyncio.run(main())
`
  try {
    runPython(script)
    console.log(`[cleanup] 已删除功能验收账号 ${username}`)
  } catch (error) {
    console.warn(`[cleanup] 清理失败：${error.message}`)
  }
}

async function run() {
  const data = setupData()
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
  })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    locale: 'zh-CN',
  })
  await context.addInitScript((token) => {
    localStorage.setItem('asktofit.token', token)
  }, data.token)
  const page = await context.newPage()

  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('favicon')) {
      errors.push(message.text())
    }
  })

  try {
    // ---------- 会话归档 / 恢复 ----------
    await page.goto(`${BASE_URL}/chat`, { waitUntil: 'domcontentloaded' })
    const item = page.locator('.sidebar__item').filter({ hasText: '归档验收会话' }).first()
    await item.waitFor({ timeout: 30000 })
    await item.hover()
    await item.locator('.sidebar__item-menu').click()
    await page
      .locator('.el-dropdown-menu:visible .el-dropdown-menu__item')
      .filter({ hasText: '归档' })
      .click()
    await page
      .locator('.el-message-box')
      .getByRole('button', { name: '归档', exact: true })
      .click()
    await page.locator('.sidebar__archive-toggle').waitFor({ timeout: 15000 })
    if (await item.isVisible()) throw new Error('归档后会话仍显示在活动列表')
    console.log('[features] 会话归档成功')

    await page.locator('.sidebar__archive-toggle').click()
    const archivedItem = page
      .locator('.sidebar__item.is-archived')
      .filter({ hasText: '归档验收会话' })
      .first()
    await archivedItem.waitFor({ timeout: 15000 })
    await archivedItem.hover()
    await archivedItem.locator('.sidebar__item-menu').click()
    await page
      .locator('.el-dropdown-menu:visible .el-dropdown-menu__item')
      .filter({ hasText: '取消归档' })
      .click()
    await page.locator('.sidebar__archive-toggle').waitFor({ state: 'hidden', timeout: 15000 })
    console.log('[features] 会话恢复成功')

    // ---------- 知识缺口运营台 ----------
    await page.locator('.shell__nav a[href="/knowledge-gaps"]').click()
    await page.waitForURL('**/knowledge-gaps')
    await page.locator('.gap-stat').first().waitFor({ timeout: 30000 })
    const statCount = await page.locator('.gap-stat').count()
    if (statCount !== 4) throw new Error(`缺口统计卡片数量异常：${statCount}`)

    const card = page.locator('.gap-card').filter({ hasText: question }).first()
    await card.waitFor({ timeout: 30000 })
    await card.getByText('商品咨询').first().waitFor()
    await card.getByText('BERT · 91%').waitFor()
    await card.getByRole('button', { name: '添加备注' }).click()
    const dialog = page.locator('.el-dialog:visible')
    await dialog.locator('textarea').fill('已联系商品团队补充定制服务说明')
    await dialog.getByRole('button', { name: '保存' }).click()
    await card.getByText('已联系商品团队补充定制服务说明').waitFor({ timeout: 15000 })
    console.log('[features] 缺口列表、意图来源与备注保存正常')

    await card.getByRole('button', { name: '标记已解决' }).click()
    await page.getByText('已标记为解决').waitFor({ timeout: 15000 })
    await card.waitFor({ state: 'hidden', timeout: 15000 })
    const resolvedStat = page.locator('.gap-stat--mint strong')
    await page.waitForFunction(
      () => document.querySelector('.gap-stat--mint strong')?.textContent?.trim() === '1',
      { timeout: 15000 },
    )
    if ((await resolvedStat.innerText()).trim() !== '1') {
      throw new Error('解决后统计未更新')
    }
    console.log('[features] 缺口状态更新与统计刷新正常')

    // ---------- 管理员评估看板 ----------
    const evalNav = page.locator('.shell__nav a[href="/evaluation"]')
    await evalNav.waitFor({ timeout: 30000 })
    await evalNav.click()
    await page.waitForURL('**/evaluation')
    await page.locator('.eval-stat').first().waitFor({ timeout: 30000 })
    const evalStatCount = await page.locator('.eval-stat').count()
    if (evalStatCount !== 4) throw new Error(`评估统计卡片数量异常：${evalStatCount}`)
    await page.getByText('100.0%').first().waitFor({ timeout: 15000 })
    await page.locator('.eval-trend__group').first().waitFor({ timeout: 15000 })

    const runRow = page
      .locator('.eval-table .el-table__body tr.el-table__row')
      .filter({ hasText: 'qa_eval.jsonl' })
      .first()
    await runRow.waitFor({ timeout: 30000 })
    await runRow.getByRole('button', { name: '详情' }).click()

    const drawer = page.locator('.el-drawer:visible')
    await drawer.getByText('生成质量（官方 RAGAS）').waitFor({ timeout: 30000 })
    await drawer.locator('.eval-metric').first().waitFor()
    const metricCount = await drawer.locator('.eval-metric').count()
    if (metricCount < 8) throw new Error(`详情指标数量异常：${metricCount}`)

    const caseRows = drawer.locator('.el-table__body tr.el-table__row')
    await caseRows.first().waitFor({ timeout: 30000 })
    if ((await caseRows.count()) !== 2) {
      throw new Error(`逐题记录数量异常：${await caseRows.count()}`)
    }
    await drawer.locator('.el-table__expand-icon').nth(1).click()
    await drawer.getByText('检索失败：模拟压测异常').waitFor({ timeout: 15000 })
    console.log('[features] 评估概览、运行表与逐题详情正常')

    await drawer.locator('.el-switch').click()
    await page.waitForFunction(
      () =>
        document.querySelectorAll('.el-drawer .el-table__body tr.el-table__row').length === 1,
      { timeout: 15000 },
    )
    console.log('[features] 评估失败项筛选正常')
    console.log('[features] 管理员导航与评估看板正常')

    if (errors.length) throw new Error(`页面错误：\n${errors.join('\n')}`)
    console.log('\n[features] 全部新增功能场景通过')
  } finally {
    await browser.close()
    cleanupData(data.user_id)
  }
}

run().catch((error) => {
  console.error('[features] 验收失败：', error)
  process.exitCode = 1
})
