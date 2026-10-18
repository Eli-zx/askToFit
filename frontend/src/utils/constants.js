export const API_BASE = (import.meta.env.VITE_API_BASE || '/api').replace(/\/+$/, '')

export const DOCUMENT_STATUS = {
  pending: { label: '排队中', type: 'info' },
  processing: { label: '解析中', type: 'warning' },
  completed: { label: '已就绪', type: 'success' },
  skipped: { label: '内容未变化', type: 'info' },
  failed: { label: '解析失败', type: 'danger' },
}

export const EMBEDDING_MODES = [
  {
    value: 'hybrid',
    label: '混合检索',
    description: '稠密语义 + 稀疏关键词，召回更稳，适合政策文档',
  },
  {
    value: 'dense',
    label: '稠密检索',
    description: '纯语义向量检索，适合表述多变的问答内容',
  },
]

export const INTENT_META = {
  return_exchange: { label: '退换货', tone: 'violet' },
  invoice: { label: '发票', tone: 'blue' },
  logistics: { label: '物流', tone: 'mint' },
  payment: { label: '支付结算', tone: 'orange' },
  refund: { label: '退款与资金安全', tone: 'red' },
  sizing: { label: '尺码', tone: 'pink' },
  membership: { label: '会员与优惠', tone: 'gold' },
  product: { label: '商品咨询', tone: 'teal' },
  greeting: { label: '问候', tone: 'gray' },
  other: { label: '综合咨询', tone: 'gray' },
}

export const SAMPLE_QUESTIONS = [
  {
    icon: 'Van',
    title: '满多少包邮？',
    text: '满多少包邮？偏远地区运费怎么算？',
    accent: 'mint',
  },
  {
    icon: 'Tickets',
    title: '发票开具时效',
    text: '开发票要多久？电子普票和专票有什么区别？',
    accent: 'violet',
  },
  {
    icon: 'Refresh',
    title: '退换货规则',
    text: '七天无理由退货需要保留吊牌吗？运费谁承担？',
    accent: 'peach',
  },
  {
    icon: 'User',
    title: '尺码推荐',
    text: '我 165cm、55kg，想买宽松一点的针织衫，选什么码？',
    accent: 'blue',
  },
]

export const FILE_ACCEPT =
  '.pdf,.ppt,.pptx,.md,.markdown,.doc,.docx,.png,.jpg,.jpeg'

export const MINERU_EXTENSIONS = [
  'pdf',
  'ppt',
  'pptx',
  'doc',
  'docx',
  'png',
  'jpg',
  'jpeg',
]

export const MAX_UPLOAD_MB = 50
