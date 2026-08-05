import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUserStore = defineStore('user', () => {
  // 从 localStorage 初始化状态，防止刷新丢失
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')

  // 登录动作：保存 token 和 用户名
  const setLoginState = (newToken: string, newUsername: string) => {
    token.value = newToken
    username.value = newUsername
    localStorage.setItem('token', newToken)
    localStorage.setItem('username', newUsername)
  }

  // 登出动作：清除状态
  const clearLoginState = () => {
    token.value = ''
    username.value = ''
    localStorage.removeItem('token')
    localStorage.removeItem('username')
  }

  return { token, username, setLoginState, clearLoginState }
})
