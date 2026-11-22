import { expect, test } from '../browser-test'
import { mockAdmin } from '../browser-fixtures'

for (const theme of ['dark', 'light']) {
  test(`${theme} 命令沙箱与其他设置下拉框反复打开时使用完整行高亮`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem('theme', value), theme)
    await mockAdmin(page)
    await page.goto('/agents')
    for (const label of ['命令沙箱', '自动记忆']) {
      const trigger = page.getByRole('combobox', { name: label, exact: true })
      const original = await trigger.textContent()
      for (const method of ['keyboard', 'mouse', 'keyboard']) {
        if (method === 'keyboard') {
          await trigger.focus()
          await trigger.press('ArrowDown')
        } else await trigger.click()
        const focused = page.locator('.ui-select-item[data-highlighted]')
        await expect(focused).toBeFocused()
        await expect(focused).toHaveCSS('outline-style', 'none')
        await expect(focused).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
        await page.keyboard.press('ArrowDown')
        await expect(page.locator('.ui-select-item[data-highlighted]')).toBeFocused()
        await expect(page.locator('.ui-select-item[data-highlighted]')).toHaveCSS(
          'outline-style',
          'none',
        )
        await page.keyboard.press('Escape')
        await expect(trigger).toBeFocused()
        await expect(trigger).toHaveText(original || '')
        await expect(page.getByRole('listbox')).toHaveCount(0)
      }
    }
  })

  test(`${theme} 共用下拉框和菜单以行高亮显示焦点，普通按钮保留焦点边框`, async ({ page }) => {
    await page.addInitScript((value) => localStorage.setItem('theme', value), theme)
    await mockAdmin(page)
    await page.goto('/dev/components')
    const button = page.getByRole('button', { name: '显示通知', exact: true })
    await button.focus()
    await page.keyboard.press('Tab')
    await page.keyboard.press('Shift+Tab')
    await expect(button).toBeFocused()
    await expect(button).toHaveCSS('outline-style', 'solid')
    await expect(button).toHaveCSS('outline-width', '2px')

    const select = page.getByRole('combobox', { name: '固定选项', exact: true })
    await select.focus()
    await select.press('Enter')
    const claude = page.getByRole('option', { name: /^Claude/ })
    await expect(claude).toBeFocused()
    await expect(claude).toHaveCSS('outline-style', 'none')
    await page.keyboard.press('ArrowDown')
    const codex = page.getByRole('option', { name: /^Codex/ })
    await expect(codex).toBeFocused()
    await expect(codex).toHaveCSS('outline-style', 'none')
    await page.keyboard.press('Enter')
    await expect(select).toContainText('Codex')
    await expect(select).toBeFocused()

    const search = page.getByRole('combobox', { name: '搜索候选', exact: true })
    await search.fill('cod')
    await search.press('ArrowDown')
    await expect(search).toBeFocused()
    await expect(search).toHaveCSS('outline-style', 'none')
    await expect(page.locator('.ui-combobox:focus-within')).not.toHaveCSS('box-shadow', 'none')
    await expect(page.locator('.ui-combobox-item[data-highlighted]')).not.toHaveCSS(
      'background-color',
      'rgba(0, 0, 0, 0)',
    )
    await search.press('Enter')
    await expect(search).toHaveValue('codex')

    const menuTrigger = page.getByRole('button', { name: '更多操作', exact: true })
    await menuTrigger.focus()
    await menuTrigger.press('ArrowDown')
    const copy = page.getByRole('menuitem', { name: '复制', exact: true })
    await expect(copy).toBeFocused()
    await expect(copy).toHaveAttribute('data-highlighted', '')
    await expect(copy).toHaveCSS('outline-style', 'none')
    await expect(copy).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    await page.keyboard.press('ArrowDown')
    const remove = page.getByRole('menuitem', { name: '删除', exact: true })
    await expect(remove).toBeFocused()
    await expect(remove).toHaveCSS('outline-style', 'none')
    await page.keyboard.press('Escape')
    await expect(menuTrigger).toBeFocused()
  })
}
