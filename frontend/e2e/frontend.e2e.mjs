/**
 * AskToFit 前端端到端验收脚本（Playwright Core + 本机 Chromium/Edge）。
 *
 * 覆盖：注册（含图形验证码）、登录、创建会话、SSE 流式问答与知识引用、
 * 知识库列表、创建知识库、上传 Markdown、解析入库、MinerU 深度解析（镜像可用时）、
 * 分块搜索/编辑、404。
 *
 * 图形验证码从本地 Redis 读取，仅用于开发环境验收。
 * 运行：node e2e/frontend.e2e.mjs
 */
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright-core'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND_DIR = path.resolve(__dirname, '..')
const PROJECT_DIR = path.resolve(FRONTEND_DIR, '..')
const ARTIFACT_DIR = path.join(FRONTEND_DIR, 'e2e-artifacts')
const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5173'
const API_BASE = process.env.E2E_API_BASE || 'http://127.0.0.1:8000/api'

const stamp = Date.now().toString(36)
const username = `e2e_${stamp}`
const password = 'AtfTest123!'
const kbName = `E2E 临时知识库 ${stamp}`
const docName = 'e2e-knowledge.md'
const mineruDocName = '尺码与版型指南.pdf'
const marker = 'E2E-ATF-2026'
const editMarker = 'E2E-EDIT-OK'

const consoleErrors = []
const pageErrors = []
const apiErrors = []

fs.mkdirSync(ARTIFACT_DIR, { recursive: true })

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

function readLatestCaptcha() {
  const script = `
import asyncio
from app.core.redis_client import get_redis_client

async def main():
    client = get_redis_client()
    best = (0, "")
    for key in await client.keys("captcha:*"):
        value = await client.get(key)
        ttl = await client.ttl(key)
        if value and ttl > best[0]:
            best = (ttl, value)
    print(best[1])
    await client.aclose()

asyncio.run(main())
`
  const output = execFileSync('uv', ['run', 'python', '-c', script], {
    cwd: PROJECT_DIR,
    encoding: 'utf8',
    timeout: 60000,
  })
  const code = output.trim().split(/\r?\n/).pop() || ''
  if (!code) throw new Error('未能从 Redis 读取验证码')
  return code
}

function cleanupUser() {
  const script = `
import asyncio
from sqlalchemy import text
from app.db.session import get_engine, dispose_engine

async def main():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE username = :name"), {"name": "${username}"})
    await dispose_engine()

asyncio.run(main())
`
  try {
    execFileSync('uv', ['run', 'python', '-c', script], {
      cwd: PROJECT_DIR,
      encoding: 'utf8',
      timeout: 60000,
    })
    console.log(`[cleanup] 已删除测试账号 ${username}`)
  } catch (error) {
    console.warn(`[cleanup] 删除测试账号失败：${error.message}`)
  }
}

async function waitForCaptcha(page) {
  const image = page.locator('img[alt="图形验证码"]')
  await image.waitFor({ state: 'visible', timeout: 20000 })
  await page.waitForFunction(() => {
    const element = document.querySelector('img[alt="图形验证码"]')
    return Boolean(element?.getAttribute('src')?.startsWith('data:image'))
  })
  return readLatestCaptcha()
}

async function screenshot(page, name) {
  await page.screenshot({
    path: path.join(ARTIFACT_DIR, `${name}.png`),
    fullPage: false,
  })
}

async function run() {
  const browser = await chromium.launch({
    executablePath: findBrowser(),
    headless: true,
  })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    locale: 'zh-CN',
  })
  const page = await context.newPage()

  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    const source = message.location()?.url || ''
    if (source.includes('favicon') || text.includes('Download the Vue Devtools')) return
    consoleErrors.push(`${text}${source ? ` (${source})` : ''}`)
  })
  page.on('response', (response) => {
    if (!response.url().includes('/api/')) return
    if (response.status() >= 400) {
      apiErrors.push(`${response.status()} ${response.request().method()} ${response.url()}`)
    }
  })

  let token = ''
  let tempKbId = null
  let kbDeleted = false
  let failure = null

  try {
    console.log(`[e2e] 浏览器：${findBrowser()}`)
    console.log(`[e2e] 测试账号：${username}`)

    // ---------- 注册 ----------
    await page.goto(`${BASE_URL}/register`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('h2:has-text("创建你的工作台")')
    const registerCaptcha = await waitForCaptcha(page)

    await page.getByPlaceholder('3-32 位字母、数字或下划线').fill(username)
    await page.getByPlaceholder('至少 6 位字符').fill(password)
    await page.getByPlaceholder('请再次输入密码').fill(password)
    await page.locator('.captcha input').fill(registerCaptcha)
    await screenshot(page, '01-register')
    await page.getByRole('button', { name: '注册账号' }).click()
    await page.waitForURL('**/login**', { timeout: 30000 })
    console.log('[e2e] 注册成功，已跳转登录页')

    // ---------- 登录 ----------
    await page.waitForSelector('h2:has-text("登录 AskToFit")')
    const loginCaptcha = await waitForCaptcha(page)
    await page.getByPlaceholder('请输入账号名称').fill(username)
    await page.getByPlaceholder('请输入登录密码').fill(password)
    await page.locator('.captcha input').fill(loginCaptcha)
    await page.getByRole('button', { name: '登录并进入工作台' }).click()
    await page.waitForURL('**/chat**', { timeout: 30000 })
    token = await page.evaluate(() => localStorage.getItem('asktofit.token') || '')
    if (!token) throw new Error('登录后未写入本地 Token')
    console.log('[e2e] 登录成功')

    // ---------- 知识库列表 ----------
    await page.locator('.shell__nav a[href="/knowledge"]').click()
    await page.waitForURL('**/knowledge')
    // 新账号也可能看到其他账号的公共知识库，允许空态或已有卡片
    await page
      .locator('.knowledge-empty, .knowledge-card:not(.knowledge-card--skeleton)')
      .first()
      .waitFor({ timeout: 30000 })
    await screenshot(page, '02-knowledge-list')
    console.log('[e2e] 知识库列表加载正常（可能包含公共知识库）')

    // ---------- 创建临时知识库 ----------
    await page.getByRole('button', { name: '创建知识库' }).first().click()
    const kbDialog = page.locator('.el-dialog:visible')
    await kbDialog.waitFor({ timeout: 10000 })
    await kbDialog.getByPlaceholder('例如：服装售前知识库').fill(kbName)
    await kbDialog
      .getByPlaceholder('简要说明知识库覆盖的业务范围')
      .fill('自动化验收临时知识库，测试完成后自动删除')
    await kbDialog.getByRole('button', { name: '创建知识库', exact: true }).click()
    const tempCard = page.locator('.knowledge-card').filter({ hasText: kbName }).first()
    await tempCard.waitFor({ timeout: 30000 })
    console.log('[e2e] 临时知识库创建成功')

    // ---------- 上传 Markdown 并等待解析入库 ----------
    await tempCard.click()
    await page.waitForURL(/\/knowledge\/\d+$/)
    tempKbId = Number(page.url().match(/\/knowledge\/(\d+)$/)?.[1])
    await page.getByRole('button', { name: '上传文档' }).first().click()
    const uploadDialog = page.locator('.el-dialog:visible')
    await uploadDialog.waitFor({ timeout: 10000 })
    await uploadDialog
      .locator('.upload__drop input[type="file"]')
      .setInputFiles(path.join(__dirname, docName))
    await uploadDialog.locator('.upload__file-name').filter({ hasText: docName }).waitFor()
    await uploadDialog.getByRole('button', { name: '上传并开始解析' }).click()
    await uploadDialog.waitFor({ state: 'hidden', timeout: 120000 })

    const row = page.locator('.el-table__row').filter({ hasText: docName }).first()
    await row.waitFor({ timeout: 30000 })
    await row
      .locator('.status-pill')
      .filter({ hasText: '已就绪' })
      .waitFor({ timeout: 300000 })
    await screenshot(page, '03-document-ready')
    console.log('[e2e] Markdown 上传、切块、向量化完成')

    // ---------- MinerU 深度解析（镜像可用时） ----------
    await page.getByRole('button', { name: '上传文档' }).first().click()
    const mineruDialog = page.locator('.el-dialog:visible')
    await mineruDialog.waitFor({ timeout: 10000 })
    await mineruDialog
      .locator('.upload__drop input[type="file"]')
      .setInputFiles(
        path.join(PROJECT_DIR, 'data', 'business_docs', mineruDocName),
      )
    await mineruDialog
      .locator('.upload__file-name')
      .filter({ hasText: mineruDocName })
      .waitFor({ timeout: 10000 })
    const mineruSwitch = mineruDialog.locator('.upload__parser .el-switch')
    await mineruSwitch.waitFor({ timeout: 10000 })
    // 等待 /api/parsers/options 返回，避免把“检测中”误判为不可用
    await mineruSwitch.waitFor({ state: 'visible', timeout: 10000 })
    await page.waitForFunction(() => {
      const element = document.querySelector(
        '.el-dialog .upload__parser .el-switch',
      )
      return element && !element.classList.contains('is-loading')
    })
    const mineruDisabled = await mineruSwitch.evaluate((element) =>
      element.classList.contains('is-disabled'),
    )
    if (mineruDisabled) {
      await mineruDialog.getByRole('button', { name: '取消' }).click()
      await mineruDialog.waitFor({ state: 'hidden', timeout: 10000 })
      console.log('[e2e] MinerU 镜像未就绪，跳过深度解析场景')
    } else {
      await mineruSwitch.click()
      await mineruDialog
        .getByRole('button', { name: '上传并开始解析' })
        .click()
      await mineruDialog.waitFor({ state: 'hidden', timeout: 120000 })

      const mineruRow = page
        .locator('.el-table__row')
        .filter({ hasText: mineruDocName })
        .first()
      await mineruRow.waitFor({ timeout: 30000 })
      const outcome = await Promise.race([
        mineruRow
          .locator('.status-pill')
          .filter({ hasText: '已就绪' })
          .waitFor({ timeout: 900000 })
          .then(() => 'ready')
          .catch(() => 'timeout'),
        mineruRow
          .locator('.status-pill')
          .filter({ hasText: '解析失败' })
          .waitFor({ timeout: 900000 })
          .then(() => 'failed')
          .catch(() => 'timeout'),
      ])
      if (outcome !== 'ready') {
        const detail = await mineruRow.innerText()
        throw new Error(`MinerU 深度解析未完成：${outcome}\n${detail}`)
      }
      await mineruRow
        .locator('.doc-cell__action.is-mineru')
        .waitFor({ timeout: 10000 })
      await screenshot(page, '03b-mineru-ready')
      console.log('[e2e] MinerU 深度解析完成')
    }

    // ---------- 分块预览 / 搜索 / 编辑 ----------
    await row.getByRole('button', { name: '分块预览' }).first().click()
    await page.waitForURL(/\/documents\/\d+\/chunks$/)
    await page.locator('.chunk-card').first().waitFor({ timeout: 30000 })
    const initialChunks = await page.locator('.chunk-card').count()
    if (!initialChunks) throw new Error('分块列表为空')

    await page.getByPlaceholder('搜索分块内容').fill(marker)
    await page.locator('.chunk__toolbar').getByRole('button', { name: '搜索' }).click()
    await page
      .locator('.chunk-card__content')
      .filter({ hasText: marker })
      .first()
      .waitFor({ timeout: 30000 })

    const chunkCard = page.locator('.chunk-card').first()
    await chunkCard.getByRole('button', { name: '编辑' }).click()
    const editDialog = page.locator('.el-dialog:visible')
    await editDialog.waitFor({ timeout: 10000 })
    const textarea = editDialog.locator('textarea')
    const original = await textarea.inputValue()
    await textarea.fill(`${original}\n\n${editMarker}`)
    await editDialog.getByRole('button', { name: '保存并同步' }).click()
    await page
      .getByText('分块已更新，并同步刷新向量')
      .waitFor({ timeout: 60000 })
    await screenshot(page, '04-chunk-preview')
    console.log('[e2e] 分块搜索与编辑同步成功')

    // ---------- 创建会话 + SSE 流式问答 ----------
    await page.locator('.shell__nav a[href="/chat"]').click()
    await page.locator('.chat-welcome').waitFor({ timeout: 30000 })
    await page
      .getByPlaceholder('输入你的售前问题，例如：满 199 元包邮吗？')
      .fill('星辰会员的积分怎么累计？')
    await page
      .getByPlaceholder('输入你的售前问题，例如：满 199 元包邮吗？')
      .press('Enter')

    const createDialog = page.locator('.el-dialog:visible')
    await createDialog.waitFor({ timeout: 10000 })
    await createDialog.getByText(kbName).first().waitFor({ timeout: 15000 })
    await createDialog.getByRole('button', { name: '创建并开始' }).click()

    await page.locator('.msg--user').first().waitFor({ timeout: 30000 })
    const assistant = page.locator('.msg--assistant').first()
    await assistant.waitFor({ timeout: 30000 })
    await assistant
      .locator('.msg__actions')
      .waitFor({ state: 'visible', timeout: 180000 })

    const answer = (await assistant.locator('.markdown-body').innerText()).trim()
    if (answer.length < 30) throw new Error(`助手回答内容过短：${answer}`)
    const sourceToggle = assistant.locator('.msg__sources-toggle')
    if ((await sourceToggle.count()) === 0) throw new Error('助手回答未展示知识引用')
    await sourceToggle.click()
    const sourceCount = await assistant.locator('.msg__source').count()
    if (!sourceCount) throw new Error('展开后没有引用资料')
    await screenshot(page, '05-chat-stream')
    console.log(`[e2e] SSE 问答完成，回答 ${answer.length} 字，引用 ${sourceCount} 条`)

    // ---------- 清理临时知识库 ----------
    await page.goto(`${BASE_URL}/knowledge`, { waitUntil: 'domcontentloaded' })
    const cleanupCard = page.locator('.knowledge-card').filter({ hasText: kbName }).first()
    await cleanupCard.waitFor({ timeout: 30000 })
    await cleanupCard.hover()
    await cleanupCard.locator('.knowledge-card__menu button').click()
    await page
      .locator('.el-dropdown-menu:visible .el-dropdown-menu__item')
      .filter({ hasText: '删除知识库' })
      .click()
    await page
      .locator('.el-message-box')
      .getByRole('button', { name: '确认删除' })
      .click()
    await cleanupCard.waitFor({ state: 'detached', timeout: 30000 })
    kbDeleted = true
    console.log('[e2e] 临时知识库已清理')

    // ---------- 404 ----------
    await page.goto(`${BASE_URL}/definitely-not-a-page`, {
      waitUntil: 'domcontentloaded',
    })
    await page.locator('.notfound__code').filter({ hasText: '404' }).waitFor({
      timeout: 15000,
    })
    await screenshot(page, '06-not-found')
    console.log('[e2e] 404 页面正常')
  } catch (error) {
    failure = error
    try {
      await screenshot(page, 'failure')
    } catch {
      // 忽略截图失败
    }
    console.error(`[e2e] 失败：${error.stack || error.message}`)
  } finally {
    // 兜底清理：UI 清理失败时通过 API 删除临时知识库
    if (token && tempKbId && !kbDeleted) {
      try {
        const response = await page.evaluate(
          async ({ apiBase, id, authToken }) => {
            const result = await fetch(`${apiBase}/knowledge_bases/${id}`, {
              method: 'DELETE',
              headers: { Authorization: `Bearer ${authToken}` },
            })
            return result.status
          },
          { apiBase: API_BASE, id: tempKbId, authToken: token },
        )
        console.log(`[cleanup] API 删除临时知识库，状态码 ${response}`)
      } catch (error) {
        console.warn(`[cleanup] API 删除知识库失败：${error.message}`)
      }
    }
    await context.close()
    await browser.close()
    cleanupUser()
  }

  console.log('\n===== E2E 结果 =====')
  console.log(`页面异常：${pageErrors.length}`)
  pageErrors.forEach((item) => console.log(`  - ${item}`))
  console.log(`控制台错误：${consoleErrors.length}`)
  consoleErrors.forEach((item) => console.log(`  - ${item}`))
  console.log(`API 错误响应：${apiErrors.length}`)
  apiErrors.forEach((item) => console.log(`  - ${item}`))
  console.log(`截图目录：${ARTIFACT_DIR}`)

  if (failure) {
    process.exitCode = 1
    throw failure
  }
  if (pageErrors.length || consoleErrors.length) {
    process.exitCode = 1
    throw new Error('页面存在未捕获异常或控制台错误')
  }
  console.log('[e2e] 全部场景通过')
}

run().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
