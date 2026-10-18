/**
 * 公共知识库前端验收：demo 账号查看 eli 创建的公共库，并确认只读与对话可选。
 *
 * 运行：node e2e/public-kb.e2e.mjs
 * 前置：后端 8000、前端 5173 已启动，eli 的公共知识库已通过导入脚本创建。
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

const USERNAME = process.env.E2E_USERNAME || 'demo'
const PASSWORD = process.env.E2E_PASSWORD || 'demo1234'
const PUBLIC_KB = '服装售前知识库（2026 春夏）'
const OWN_KB = '服装售前知识库'

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

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function login(page) {
  await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('h2:has-text("登录 AskToFit")')
  const captcha = await waitForCaptcha(page)
  await page.getByPlaceholder('请输入账号名称').fill(USERNAME)
  await page.getByPlaceholder('请输入登录密码').fill(PASSWORD)
  await page.locator('.captcha input').fill(captcha)
  await page.getByRole('button', { name: '登录并进入工作台' }).click()
  await page.waitForURL('**/chat**', { timeout: 30000 })
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

  const consoleErrors = []
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('console', (message) => {
    if (message.type() !== 'error') return
    const text = message.text()
    const source = message.location()?.url || ''
    if (source.includes('favicon') || text.includes('Download the Vue Devtools')) return
    consoleErrors.push(`${text}${source ? ` (${source})` : ''}`)
  })

  let createdConversationId = null
  try {
    console.log(`[e2e] 浏览器：${findBrowser()}`)
    console.log(`[e2e] 账号：${USERNAME}`)
    await login(page)

    // ---------- 知识库列表：公共库与自有库 ----------
    await page.locator('.shell__nav a[href="/knowledge"]').click()
    await page.waitForURL('**/knowledge')

    const publicCard = page.locator('.knowledge-card').filter({ hasText: PUBLIC_KB }).first()
    await publicCard.waitFor({ timeout: 30000 })
    await publicCard.locator('.knowledge-card__public').waitFor({ timeout: 10000 })
    assert(
      (await publicCard.locator('.knowledge-card__menu').count()) === 0,
      '公共知识库不应出现管理菜单',
    )
    assert(
      (await publicCard.getByText('查看文档', { exact: true }).count()) > 0,
      '公共知识库卡片应显示“查看文档”',
    )
    console.log('[e2e] 公共知识库在 demo 账号可见，且无管理菜单')

    const ownCard = page
      .locator('.knowledge-card')
      .filter({ hasText: OWN_KB })
      .filter({ hasNotText: PUBLIC_KB })
      .first()
    if (await ownCard.count()) {
      assert(
        (await ownCard.locator('.knowledge-card__menu').count()) > 0,
        '自有知识库应显示管理菜单',
      )
      console.log('[e2e] 自有知识库管理菜单正常')
    }
    await screenshot(page, 'public-kb-01-list')

    // ---------- 公共库详情：只读 ----------
    await publicCard.click()
    await page.waitForURL(/\/knowledge\/\d+$/)
    await page.locator('.detail__readonly').waitFor({ timeout: 30000 })
    assert(
      (await page.getByRole('button', { name: '上传文档' }).count()) === 0,
      '公共知识库详情不应出现上传按钮',
    )
    assert(
      (await page.getByRole('button', { name: '编辑信息' }).count()) === 0,
      '公共知识库详情不应出现编辑按钮',
    )
    const rows = page.locator('.el-table__row')
    await rows.first().waitFor({ timeout: 30000 })
    const rowCount = await rows.count()
    assert(rowCount >= 9, `公共知识库应包含至少 9 篇文档，实际 ${rowCount}`)
    assert(
      (await page.getByText('公共知识库文档仅供查看与检索。').count()) > 0,
      '公共库应显示只读说明',
    )
    console.log(`[e2e] 公共知识库详情只读正常，文档 ${rowCount} 篇`)
    await screenshot(page, 'public-kb-02-detail')

    // ---------- 对话设置：公共库可选 ----------
    await page.locator('.shell__nav a[href="/chat"]').click()
    await page.waitForURL('**/chat**')
    await page.getByRole('button', { name: '新建对话' }).click()
    const dialog = page.locator('.el-dialog:visible')
    await dialog.waitFor({ timeout: 10000 })
    await dialog.locator('.el-select').first().click()
    const option = page
      .locator('.el-select-dropdown:visible .el-select-dropdown__item')
      .filter({ hasText: PUBLIC_KB })
      .first()
    await option.waitFor({ timeout: 10000 })
    assert(
      (await option.getByText('公共', { exact: true }).count()) > 0,
      '公共知识库选项应带“公共”标签',
    )
    // 新建会话默认会选中第一个知识库；若公共库已选中，不要再点击（否则会取消）
    const alreadySelected = await option.evaluate((element) =>
      element.classList.contains('is-selected'),
    )
    if (alreadySelected) {
      await page.keyboard.press('Escape')
    } else {
      await option.click()
    }
    await page
      .locator('.el-select-dropdown:visible')
      .waitFor({ state: 'hidden', timeout: 10000 })
      .catch(() => {})
    await page.waitForTimeout(300)
    await dialog.getByRole('button', { name: '创建并开始' }).click()
    await page.waitForURL(/\/chat\/\d+/, { timeout: 30000 })
    createdConversationId = Number(page.url().match(/\/chat\/(\d+)/)?.[1])
    console.log(`[e2e] demo 可使用公共知识库创建会话 id=${createdConversationId}`)
    await screenshot(page, 'public-kb-03-chat')

    if (consoleErrors.length) {
      throw new Error(`控制台错误：${consoleErrors.join(' | ')}`)
    }
    if (pageErrors.length) {
      throw new Error(`页面错误：${pageErrors.join(' | ')}`)
    }
    console.log('\n公共知识库前端验收通过')
  } catch (error) {
    console.error(`[e2e] 当前地址：${page.url()}`)
    await screenshot(page, 'public-kb-failure').catch(() => {})
    throw error
  } finally {
    if (createdConversationId) {
      const token = await page
        .evaluate(() => localStorage.getItem('asktofit.token') || '')
        .catch(() => '')
      if (token) {
        await page
          .evaluate(
            async ({ id, authToken }) => {
              await fetch(`http://127.0.0.1:8000/api/chat/conversations/${id}`, {
                method: 'DELETE',
                headers: { Authorization: `Bearer ${authToken}` },
              })
            },
            { id: createdConversationId, authToken: token },
          )
          .catch(() => {})
      }
    }
    await browser.close()
  }
}

run().catch((error) => {
  console.error(`[e2e] 失败：${error.message}`)
  process.exit(1)
})
