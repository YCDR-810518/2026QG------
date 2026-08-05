// src/stores/useAppStore.ts
import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    token: '',             // 管理员 Token
    adminInfo: null,       // 管理员信息
    realtimeDensity: [],   // 实时人流密度数据 (大屏用)
  }),
  actions: {
    setToken(newToken: string) {
      this.token = newToken
    }
  }
})
