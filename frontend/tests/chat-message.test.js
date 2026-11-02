import { mount } from '@vue/test-utils'

import ChatMessage from '@/components/ChatMessage.vue'

function renderMessage(overrides = {}) {
  return mount(ChatMessage, {
    props: {
      message: {
        role: 'assistant',
        content: '满 199 元包邮。',
        intent: 'logistics',
        intent_source: 'bert',
        intent_confidence: 0.873,
        sources: [],
        ...overrides,
      },
    },
    global: {
      stubs: {
        ElTooltip: { template: '<span><slot /></span>' },
        ElIcon: { template: '<span><slot /></span>' },
      },
    },
  })
}

describe('ChatMessage 意图来源展示', () => {
  it('显示 BERT 来源但不显示分类概率', () => {
    const wrapper = renderMessage()
    const detail = wrapper.find('.msg__intent-detail')

    expect(detail.text()).toBe('BERT')
    expect(detail.text()).not.toContain('%')
    expect(wrapper.text()).not.toContain('87%')
  })

  it('关键词来源同样只显示来源名', () => {
    const wrapper = renderMessage({ intent_source: 'keyword' })

    expect(wrapper.find('.msg__intent-detail').text()).toBe('关键词')
  })
})
