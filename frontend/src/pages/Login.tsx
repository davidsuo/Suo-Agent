import { useState } from 'react';
import { Button, Input, Card, message } from 'antd';
import api from '../api/client';

export default function Login({ onLogin }: { onLogin: (user: any) => void }) {
  const [username, setUsername] = useState('');
  const [pin, setPin] = useState('');
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    setLoading(true);
    try {
      const res = await api.post('/login', { username, pin });
      if (res.data.status === 'success') {
        message.success('登录成功！');
        onLogin(res.data.user);
      } else {
        message.error(res.data.message);
      }
    } catch (err) {
      message.error('网络错误，请检查后端服务');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f0f2f5' }}>
      {/* 登录卡片 */}
      <Card style={{ width: 400 }}>
        
        {/* 1. 标题改为 Logo + AI原生企业 */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, marginBottom: 24 }}>
          <img src="/logo.png" alt="欣正咨询" style={{ width: 120, height: 40, objectFit: 'contain' }} />
          <span style={{ fontSize: 26, fontWeight: 'bold' }}>AI原生系统</span>
        </div>
        
        <Input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} style={{ marginBottom: 16 }} />
        <Input.Password placeholder="密码" value={pin} onChange={(e) => setPin(e.target.value)} style={{ marginBottom: 24 }} />
        <Button type="primary" block loading={loading} onClick={handleLogin}>登录</Button>
      </Card>

      {/* 2. 登录框底部添加公司名 */}
      <div style={{ textAlign: 'center', marginTop: 20, color: '#999', fontSize: 16 }}>
        上海欣正管理咨询有限公司
      </div>
    </div>
  );
}