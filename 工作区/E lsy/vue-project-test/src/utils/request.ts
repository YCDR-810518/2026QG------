import axios from 'axios'
import { useUserStore } from '../stores/user'
import router from '../router'

// 创建 axios 实例
const service = axios.create({
  // 后端 Swagger 显示运行在 127.0.0.1:8100[cite: 6]
  // 联调时如果后端换了 IP，只需要改这里即可
  baseURL: 'http://192.168.1.114:8100', 
  timeout: 5000 // 请求超时时间
})

// 请求拦截器：自动在请求头中携带 Token
service.interceptors.request.use(
  (config) => {
    const userStore = useUserStore()
    if (userStore.token) {
      // 假设后端采用 Bearer Token 规范，可视情况调整
      config.headers['Authorization'] = `Bearer ${userStore.token}`
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// 响应拦截器：统一处理错误和未授权情况
service.interceptors.response.use(
  (response) => {
    const res = response.data
    // 根据 API 文档，通用响应结构包含 code 字段[cite: 5]
    if (res.code !== 200 && res.code !== 0) {
      console.error('API 请求异常:', res.msg)
      return Promise.reject(new Error(res.msg || 'Error'))
    }
    return res // 直接返回解析后的数据 (包含 code, msg, data)
  },
  (error) => {
    // 处理 401 token 失效等情况
    if (error.response && error.response.status === 401) {
      const userStore = useUserStore()
      userStore.clearLoginState()
      router.push('/login')
    }
    return Promise.reject(error)
  }
)

export default service
