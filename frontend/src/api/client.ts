import axios from 'axios';

const api = axios.create({
  baseURL: '/api', // 走 Vite 代理，不需要写死域名
  timeout: 120000, // AI回复可能较慢，给2分钟
});

export default api;