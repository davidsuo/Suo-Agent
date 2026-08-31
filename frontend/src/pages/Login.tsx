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
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', background: '#f0f2f5' }}>
      <Card title="企业AI原生系统" style={{ width: 400 }}>
        <Input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} style={{ marginBottom: 16 }} />
        <Input.Password placeholder="密码" value={pin} onChange={(e) => setPin(e.target.value)} style={{ marginBottom: 24 }} />
        <Button type="primary" block loading={loading} onClick={handleLogin}>登录</Button>
      </Card>
    </div>
  );
}