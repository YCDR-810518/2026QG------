// src/router/index.ts
import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '../stores/user'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/login' // 默认跳转登录页
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('../views/Login.vue')
    },
    {
      path: '/admin',
      name: 'Admin',
      component: () => import('../views/Admin.vue'),
      meta: { requiresAuth: true } // 标记：需要鉴权
    },
    {
      path: '/dashboard',
      name: 'Dashboard',
      component: () => import('../views/Dashboard.vue'),
      meta: { requiresAuth: true } // 标记：需要鉴权
    }
  ]
})

// 全局前置路由守卫
router.beforeEach((to, from, next) => {
  const userStore = useUserStore()
  
  // 如果该路由需要鉴权，且用户没有 token
  if (to.meta.requiresAuth && !userStore.token) {
    next('/login') // 强制重定向回登录页
  } 
  // 如果用户已登录，且试图访问登录页
  else if (to.path === '/login' && userStore.token) {
    next('/admin') // 自动跳入后台 (或者跳入 /dashboard，看你的业务需求)
  } 
  // 其他情况正常放行
  else {
    next()
  }
})

export default router