import { useState, useRef, useEffect, useCallback } from 'react';
import { Layout, Menu, Input, Button, Avatar, message as antMessage, Tooltip, Card, Row, Col, Statistic, Table, Spin, Space, Modal, Tag, Select, Dropdown } from 'antd';
import { UserOutlined, SendOutlined, PlusOutlined, DeleteOutlined, PaperClipOutlined, SoundOutlined, LogoutOutlined, CloseOutlined, SearchOutlined, DownloadOutlined, UploadOutlined, LikeOutlined, DislikeOutlined, EditOutlined, FolderOpenOutlined, FileExcelOutlined, DownOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../api/client';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Sider, Content } = Layout;
interface Message { role: 'user' | 'assistant'; content: string; }

const roleMap: Record<string, string> = {
  admin: '管理员', manager: '经理', developer: '研发人员', viewer: '观察者'
};

export default function Chat({ user, onLogout }: { user: any, onLogout: () => void }) {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [activeView, setActiveView] = useState('chat');
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [currentProject, setCurrentProject] = useState(() => localStorage.getItem('currentProject') || '主对话');
  const [projects, setProjects] = useState(['主对话', '产品部']);
  const [pendingFile, setPendingFile] = useState<{ name: string; content: string } | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const chatFileInputRef = useRef<HTMLInputElement>(null);
  const kbFileInputRef = useRef<HTMLInputElement>(null);
  const kbBatchFileInputRef = useRef<HTMLInputElement>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const recognitionRef = useRef<any>(null);
  const [isListening, setIsListening] = useState(false);
  
  const [healthData, setHealthData] = useState<any>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [logsData, setLogsData] = useState<any[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logSearch, setLogSearch] = useState('');

  const [kbFiles, setKbFiles] = useState<any[]>([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [kbSearch, setKbSearch] = useState('');
  const [isKbUploadOpen, setIsKbUploadOpen] = useState(false);
  const [kbFile, setKbFile] = useState<any>(null);
  const [kbTags, setKbTags] = useState('');

  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [editingFile, setEditingFile] = useState<any>(null);
  const [editTags, setEditTags] = useState('');

  const [usersList, setUsersList] = useState<any[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [isUserModalOpen, setIsUserModalOpen] = useState(false);
  const [newUser, setNewUser] = useState({ username: '', pin: '', real_name: '', role: 'viewer', department: '', contact: '', status: '正常' });
  const [userSearch, setUserSearch] = useState('');

  const [statusData, setStatusData] = useState<any[]>([]);
  const [statusLoading, setStatusLoading] = useState(false);

  const sessionId = `${user.username}_${currentProject}`;

  useEffect(() => {
    localStorage.setItem('currentProject', currentProject);
    loadHistory(currentProject);
    if (activeView === 'kb') { loadKbFiles(); }
  }, [currentProject, activeView]);

  useEffect(() => {
    if (activeView === 'chat') { messagesEndRef.current?.scrollIntoView({ behavior: 'auto' }); }
  }, [messages, activeView]);

  const loadHistory = useCallback(async (project: string) => {
    const targetSessionId = `${user.username}_${project}`;
    try {
      const res = await api.get(`/history/${targetSessionId}`);
      if (res.data && res.data.status === 'success') {
        let msgs = res.data.data || [];
        const newMsgs: Message[] = [];
        for (const msg of msgs) {
          if (msg.role === 'user' && msg.content.startsWith('📎 上传文件：')) {
            const parts = msg.content.split('\n\n');
            if (parts.length >= 2) {
              const fileRef = parts[0];
              const question = parts.slice(1).join('\n\n');
              newMsgs.push({ role: 'user', content: fileRef });
              if (question) newMsgs.push({ role: 'user', content: question });
            } else newMsgs.push({ role: 'user', content: msg.content });
          } else newMsgs.push(msg);
        }
        setMessages(newMsgs);
      }
    } catch { setMessages([]); }
  }, [user.username]);

  const loadUsers = async () => {
    setUsersLoading(true);
    try {
      const res = await api.get('/users/list');
      if (res.data && res.data.status === 'success') setUsersList(res.data.data);
    } catch { antMessage.error("用户列表加载失败"); }
    finally { setUsersLoading(false); }
  };

  const loadKbFiles = async () => {
    setKbLoading(true);
    setKbSearch('');
    try {
      const res = await api.get('/kb/list');
      if (res.data && res.data.status === 'success') setKbFiles(res.data.data);
    } catch { antMessage.error("知识库列表加载失败"); }
    finally { setKbLoading(false); }
  };

  const handleKbDelete = async (fileName: string, onSuccess?: () => void, silent = false) => {
    const formData = new FormData();
    formData.append('file_name', fileName);
    try {
      const res = await api.post('/kb/delete', formData);
      if (res.data.status === 'success') {
        if (!silent) antMessage.success(`"${fileName}" 已删除`);
        if (onSuccess) onSuccess();
        loadKbFiles();
      } else antMessage.error(res.data.message || '删除失败');
    } catch (error) {
      antMessage.error('删除请求失败');
      console.error(error);
    }
  };

  const handleKbSubmit = async () => {
    if (!kbFile) { antMessage.warning("请先选择文件"); return; }
    const formData = new FormData();
    formData.append('file', kbFile);
    formData.append('tags', kbTags);
    try {
      const res = await api.post('/kb/index', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
      if (res.data.status === 'success') {
        antMessage.success("索引成功");
        setIsKbUploadOpen(false);
        setKbFile(null);
        setKbTags('');
        loadKbFiles();
      } else antMessage.error(res.data.message || '索引失败');
    } catch (error) {
      antMessage.error("索引请求失败");
      console.error(error);
    }
  };

  const handleEditTagsSubmit = async () => {
    if (!editingFile) return;
    const fd = new FormData();
    fd.append('file_name', editingFile.file_name);
    fd.append('tags', editTags);
    const res = await api.post('/kb/update_tags', fd);
    if (res.data.status === 'success') {
      antMessage.success('标签更新成功');
      setIsEditModalOpen(false);
      loadKbFiles();
    } else antMessage.error(res.data.message || '更新失败');
  };

  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SpeechRecognition) {
      const recognition = new SpeechRecognition();
      recognition.lang = 'zh-CN';
      recognition.onresult = (event: any) => { setIsListening(false); sendMessage(event.results[0][0].transcript); };
      recognition.onerror = () => setIsListening(false);
      recognition.onend = () => setIsListening(false);
      recognitionRef.current = recognition;
    }
  }, []);

  const sendMessage = async (msgText: string, fileData?: any) => {
    if ((!msgText && !fileData) || loading) return;
    const pendingFileData = fileData || pendingFile;
    const isAudio = pendingFileData?.name?.toLowerCase().match(/\.(wav|mp3|m4a|ogg|webm)$/);
    setInput('');
    setLoading(true);
    if (pendingFileData) {
      const fileDisplay = isAudio ? '🎤 语音文件' : `📎 上传文件：${pendingFileData.name}`;
      setMessages(prev => [...prev, { role: 'user', content: fileDisplay }]);
    }
    if (msgText) setMessages(prev => [...prev, { role: 'user', content: msgText }]);
    setMessages(prev => [...prev, { role: 'assistant', content: '🔍 正在处理...' }]);
    try {
      let query = msgText || '请分析该文件';
      if (pendingFileData) {
        let content = pendingFileData.content;
        query = `文件 ${pendingFileData.name} 的内容如下：\n${content}\n\n用户问题：${msgText || '请分析该文件'}`;
      }
      const payload = { session_id: sessionId, query: query, user_text: msgText || '' };
      const res = await api.post('/chat', payload);
      if (res.data.answer && res.data.answer.includes('账号已被禁用')) {
        antMessage.error('您的账号已被禁用，请重新登录！');
        setTimeout(() => handleLogout(), 1500);
        return;
      }
      const fullText = res.data.answer || '抱歉，暂时无法回答。';
      setMessages(prev => {
        const newMessages = [...prev];
        newMessages[newMessages.length - 1] = { role: 'assistant', content: fullText };
        return newMessages;
      });
    } catch (err) {
      console.error(err);
      antMessage.error('AI 响应超时或数据过长');
      setMessages(prev => {
        const newMessages = [...prev];
        newMessages[newMessages.length - 1] = { role: 'assistant', content: '抱歉，请求处理失败，请简化问题或减少文件内容。' };
        return newMessages;
      });
    } finally {
      setLoading(false);
      setPendingFile(null);
      setSelectedFile(null);
    }
  };

  const handleSend = () => sendMessage(input);
  const startRecording = () => { if (recognitionRef.current) { recognitionRef.current.start(); setIsListening(true); } };
  const stopRecording = () => { if (recognitionRef.current) { recognitionRef.current.stop(); setIsListening(false); } };

  const handleAddProject = () => {
    const p = prompt('请输入项目名');
    if (p && !projects.includes(p)) {
      setProjects([...projects, p]);
      setCurrentProject(p);
    }
  };
  const handleDeleteProject = () => {
    if (currentProject === '主对话') { antMessage.warning('不可删除主对话'); return; }
    setProjects(projects.filter(p => p !== currentProject));
    setCurrentProject('主对话');
  };

  const handleLogout = () => {
    sessionStorage.removeItem('suo_user');
    if (onLogout) onLogout();
    else window.location.href = '/';
  };

  const loadHealth = async () => {
    setActiveView('health');
    setHealthLoading(true);
    try {
      const res = await api.get('/health');
      if (res.data?.data) setHealthData(res.data.data);
    } catch { antMessage.warning("健康接口异常"); }
    finally { setHealthLoading(false); }
  };

  const loadLogs = async () => {
    setActiveView('logs');
    setLogsLoading(true);
    try {
      const res = await api.get('/logs');
      if (res.data?.data) setLogsData(res.data.data);
    } catch { antMessage.warning("日志接口异常"); }
    finally { setLogsLoading(false); }
  };

  const loadStatus = async () => {
    setActiveView('status');
    setStatusLoading(true);
    try {
      const res = await api.get('/status');
      if (res.data && res.data.status === 'success') {
        setStatusData(res.data.data);
      } else antMessage.error("获取状态数据失败");
    } catch { antMessage.error("后端状态接口异常"); }
    finally { setStatusLoading(false); }
  };

  const handleExportLogs = async () => {
    try {
      const response = await api.get('/logs/export', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      const dateStr = new Date().toISOString().slice(0,10);
      link.setAttribute('download', `logs_${dateStr}.csv`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      antMessage.success('日志导出成功');
    } catch (error) {
      console.error(error);
      antMessage.error('日志导出失败，请稍后重试');
    }
  };
  
  const downloadKbFile = async (fileName: string) => {
    try {
      const response = await api.get('/kb/download', { params: { file_name: fileName }, responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', fileName);
      document.body.appendChild(link);
      link.click();
      link.remove();
      antMessage.success(`"${fileName}" 下载成功`);
    } catch (error) {
      antMessage.error(`"${fileName}" 下载失败，请确认后端已重启`);
    }
  };

  const handleBatchUpload = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    let uploadedCount = 0;
    Array.from(files).forEach(async (file) => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('tags', '');
      try {
        await api.post('/kb/index', formData, { headers: { 'Content-Type': 'multipart/form-data' } });
        uploadedCount++;
        if (uploadedCount === files.length) {
          antMessage.success(`批量上传完成，共 ${uploadedCount} 个文件`);
          loadKbFiles();
        }
      } catch (error) { antMessage.error(`文件 ${file.name} 上传失败`); }
    });
  };

  const toolColumns = [
      { title: '工具名称', dataIndex: 'tool', render: (text: any) => text || '系统操作' },
      { title: '调用次数', dataIndex: 'count' }
  ];

  const logColumns = [
    { title: '时间戳', dataIndex: 'timestamp', width: 180 },
    { title: '操作人/窗口', dataIndex: 'username', width: 140 },
    { title: '角色', dataIndex: 'role', width: 100 },
    { title: '操作行为/内容', dataIndex: 'detail', ellipsis: true },
    { title: '调用工具', dataIndex: 'action', width: 150 },
    { title: '状态', dataIndex: 'status', width: 80 },
  ];

  const kbColumns = [
    { title: '文档名称', dataIndex: 'file_name' },
    { title: '标签', dataIndex: 'tags', width: 220, ellipsis: true },
    { title: '索引时间', dataIndex: 'created_at', width: 180 },
    { title: '切片数', dataIndex: 'chunks', width: 80 },
    {
      title: '操作',
      width: 80,
      render: (_: any, record: any) => (
        <Button type="text" icon={<EditOutlined />} onClick={() => {
          setEditingFile(record);
          setEditTags(record.tags);
          setIsEditModalOpen(true);
        }} />
      )
    }
  ];

  const statusColumns = [
    { title: 'Worker 名称', dataIndex: 'name', key: 'name' },
    { title: '运行状态', dataIndex: 'is_running', key: 'is_running', render: (running: boolean) => (running ? <Tag color="green">运行中</Tag> : <Tag color="red">已停止</Tag>) },
    { title: '完成任务数', dataIndex: 'task_count', key: 'task_count' },
    { title: '失败任务数', dataIndex: 'error_count', key: 'error_count' },
    { title: '队列长度', dataIndex: 'queue_size', key: 'queue_size' },
    { title: '平均耗时(s)', dataIndex: 'avg_time', key: 'avg_time' },
    { title: '错误率', dataIndex: 'error_rate', key: 'error_rate' },
  ];

  const filteredLogs = logsData.filter(log => (log.username || '').toLowerCase().includes(logSearch.toLowerCase()) || (log.detail || '').toLowerCase().includes(logSearch.toLowerCase()));
  const filteredKb = kbFiles.filter(f => (f.file_name || '').toLowerCase().includes(kbSearch.toLowerCase()));
  const filteredUsers = usersList.filter(u => (u.username || '').toLowerCase().includes(userSearch.toLowerCase()) || (u.real_name || '').toLowerCase().includes(userSearch.toLowerCase()) || (u.department || '').toLowerCase().includes(userSearch.toLowerCase()));

  const documentMenuItems = [
    { key: 'upload', label: '上传文档', icon: <UploadOutlined /> },
    { key: 'download', label: '下载选中', icon: <DownloadOutlined /> },
  ];

  const batchMenuItems = [
    { key: 'batch_upload', label: '批量上传', icon: <FolderOpenOutlined /> },
    { key: 'export', label: '导出列表', icon: <FileExcelOutlined /> },
  ];

  return (
    <Layout style={{ height: '100vh', width: '100%', margin: 0, padding: 0, background: '#f5f5f5' }}>
      <Sider theme="light" width={260} style={{ background: '#fff', borderRight: '1px solid #f0f0f0' }}>
        <div style={{ padding: '16px 10px', fontWeight: 'bold', fontSize: 16 }}>🚀 某某企业AI原生系统平台</div>
        <div style={{ padding: '0 10px', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <Avatar icon={<UserOutlined />} />
          <span>{user.display_name || user.real_name}{user.department && ` (${user.department}：${roleMap[user.role] || user.role})`}</span>
        </div>
        <div style={{ padding: '0 10px', marginBottom: 8, display: 'flex', gap: 8 }}>
          <Button style={{ flex: 1 }} icon={<PlusOutlined />} onClick={handleAddProject}>新建对话窗</Button>
          <Button style={{ flex: 1 }} icon={<DeleteOutlined />} onClick={handleDeleteProject} danger>删除</Button>
        </div>
        <div style={{ padding: '0 10px' }}>
          <Menu mode="inline" selectedKeys={[currentProject]} style={{ borderInlineEnd: 'none', textAlign: 'left', width: '100%' }} items={projects.map(p => ({ key: p, label: p }))} onClick={({ key }) => setCurrentProject(key)} />
        </div>
      </Sider>
      <div style={{ flex: 1, background: '#f5f5f5' }} />
      <div style={{ width: '24cm', flex: '0 0 24cm', background: '#f5f5f5', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <Content style={{ flex: 1, padding: 20, overflowY: 'auto', background: '#f5f5f5' }}>
          {activeView === 'kb' && (
            <Spin spinning={kbLoading}>
              <h2>📚 企业垂直知识库管理</h2>
              <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'flex-start' }}>
                <Space>
                  <input type="file" ref={kbFileInputRef} style={{ display: 'none' }} onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) {
                        const existing = kbFiles.find(f => f.file_name === file.name);
                        if (existing) {
                          Modal.confirm({
                            title: '文件已存在',
                            content: `文件 "${file.name}" 已存在，是否覆盖？`,
                            onOk: () => { handleKbDelete(file.name, () => { setKbFile(file); setKbTags(''); setIsKbUploadOpen(true); }); },
                            onCancel: () => antMessage.info('已取消上传')
                          });
                        } else {
                          setKbFile(file); setKbTags(''); setIsKbUploadOpen(true);
                        }
                      }
                      e.target.value = '';
                    }} />
                  <input type="file" multiple ref={kbBatchFileInputRef} style={{ display: 'none' }} onChange={(e) => { handleBatchUpload(e.target.files); e.target.value = ''; }} />
                  <Dropdown menu={{ items: documentMenuItems, onClick: ({ key }) => {
                      if (key === 'upload') kbFileInputRef.current?.click();
                      else if (key === 'download') {
                        if (selectedRowKeys.length === 0) { antMessage.warning('请先勾选要下载的文档'); return; }
                        const selectedFiles = selectedRowKeys.map(idx => filteredKb[idx as number].file_name);
                        selectedFiles.forEach(fname => downloadKbFile(fname));
                      }
                    }}}>
                    <Button icon={<DownOutlined />}>文档操作</Button>
                  </Dropdown>
                  <Button icon={<ReloadOutlined />} onClick={loadKbFiles}>刷新</Button>
                  <Dropdown menu={{ items: batchMenuItems, onClick: ({ key }) => {
                      if (key === 'batch_upload') kbBatchFileInputRef.current?.click();
                      else if (key === 'export') {
                        if (filteredKb.length === 0) { antMessage.warning('当前无可导出的文档'); return; }
                        const header = '文档名称,标签,索引时间,切片数\n';
                        const rows = filteredKb.map(f => `${f.file_name},${f.tags},${f.created_at},${f.chunks}`).join('\n');
                        const csv = header + rows;
                        const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
                        const link = document.createElement('a');
                        link.href = URL.createObjectURL(blob);
                        link.download = `知识库列表_${new Date().toISOString().slice(0,10)}.csv`;
                        link.click(); URL.revokeObjectURL(link.href); antMessage.success('导出成功');
                      }
                    }}}>
                    <Button icon={<DownOutlined />}>批量操作</Button>
                  </Dropdown>
                  <Button danger icon={<DeleteOutlined />} onClick={() => {
                    if (selectedRowKeys.length === 0) { antMessage.warning('请先勾选要删除的文档'); return; }
                    const selectedFiles = selectedRowKeys.map(idx => filteredKb[idx as number].file_name);
                    Modal.confirm({
                      title: `确定删除选中的 ${selectedFiles.length} 个文档吗？`,
                      onOk: async () => {
                        for (const fname of selectedFiles) { await handleKbDelete(fname, undefined, true); }
                        setSelectedRowKeys([]); loadKbFiles(); antMessage.success('删除完成');
                      }
                    });
                  }}>删除</Button>
                </Space>
                <Input placeholder="搜索文档名..." prefix={<SearchOutlined />} value={kbSearch} onChange={(e) => setKbSearch(e.target.value)} style={{ width: 200 }} />
              </Space>
              <Table rowSelection={{ selectedRowKeys, onChange: (selectedKeys) => setSelectedRowKeys(selectedKeys as React.Key[]) }} dataSource={filteredKb.map((item, index) => ({ ...item, key: index }))} columns={kbColumns} pagination={{ pageSize: 10 }} size="small" />
              <Modal title={`编辑标签：${editingFile?.file_name}`} open={isEditModalOpen} onCancel={() => setIsEditModalOpen(false)} onOk={handleEditTagsSubmit}>
                <Input placeholder="输入新标签（用逗号分隔）" value={editTags} onChange={(e) => setEditTags(e.target.value)} />
              </Modal>
            </Spin>
          )}
          {activeView === 'admin' && (
            <Spin spinning={usersLoading}>
              <h2>👥 用户管理</h2>
              <Space style={{ marginBottom: 16 }}>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => { setNewUser({ username: '', pin: '', real_name: '', role: 'viewer', department: '', contact: '', status: '正常' }); setIsUserModalOpen(true); }}>添加用户</Button>
                <Input.Search placeholder="查询用户/姓名/部门" allowClear onSearch={(val) => setUserSearch(val)} onChange={(e) => setUserSearch(e.target.value)} style={{ width: 220 }} />
                <Button icon={<DownloadOutlined />} onClick={() => {
                  if (filteredUsers.length === 0) return;
                  const header = '用户名,姓名,角色,部门,联系方式,状态\n';
                  const rows = filteredUsers.map(u => `${u.username},${u.real_name},${u.role},${u.department},${u.contact},${u.status}`).join('\n');
                  const blob = new Blob(['\uFEFF' + header + rows], { type: 'text/csv;charset=utf-8;' });
                  const link = document.createElement('a');
                  link.href = URL.createObjectURL(blob);
                  link.download = `用户列表_${new Date().toISOString().slice(0,10)}.csv`;
                  link.click(); URL.revokeObjectURL(link.href); antMessage.success('导出成功');
                }}>导出</Button>
                <Button danger icon={<DeleteOutlined />} onClick={() => {
                  if (selectedRowKeys.length === 0) { antMessage.warning('请先勾选要删除的用户'); return; }
                  const selectedUsers = selectedRowKeys.map(idx => filteredUsers[idx as number].username);
                  Modal.confirm({
                    title: `确定删除选中的 ${selectedUsers.length} 个用户吗？`,
                    onOk: async () => {
                      for (const uname of selectedUsers) {
                        const fd = new FormData(); fd.append('username', uname); await api.post('/users/delete', fd);
                      }
                      setSelectedRowKeys([]); loadUsers(); antMessage.success('删除完成');
                    }
                  });
                }}>删除</Button>
              </Space>
              <Table rowSelection={{ selectedRowKeys, onChange: (selectedKeys) => setSelectedRowKeys(selectedKeys as React.Key[]) }} dataSource={filteredUsers.map((u, i) => ({ ...u, key: i }))} columns={[
                  { title: '用户名', dataIndex: 'username' },
                  { title: '姓名', dataIndex: 'real_name' },
                  { title: '角色', dataIndex: 'role', render: (text: any, record: any) => (
                      <Select value={text} style={{ width: 100 }} onChange={async (val) => {
                          const fd = new FormData(); fd.append('username', record.username); fd.append('role', val); fd.append('status', record.status);
                          await api.post('/users/update', fd); antMessage.success('角色更新成功'); loadUsers();
                        }}>
                        <Select.Option value="admin">管理员</Select.Option><Select.Option value="manager">经理</Select.Option><Select.Option value="developer">研发人员</Select.Option><Select.Option value="viewer">观察者</Select.Option>
                      </Select>
                    ) },
                  { title: '部门', dataIndex: 'department' },
                  { title: '联系方式', dataIndex: 'contact' },
                  { title: '状态', dataIndex: 'status', render: (text: any, record: any) => (
                      <Select value={text} style={{ width: 100 }} onChange={(val) => {
                          Modal.confirm({
                            title: `确认将用户 "${record.username}" 设为 ${val} 吗？`,
                            content: val === '禁用' ? '禁用后该用户将无法登录系统，是否确认？' : '启用后该用户将恢复登录权限，是否确认？',
                            onOk: async () => {
                              const fd = new FormData(); fd.append('username', record.username); fd.append('role', record.role); fd.append('status', val);
                              await api.post('/users/update', fd); antMessage.success('状态更新成功'); loadUsers();
                            }
                          });
                        }}>
                        <Select.Option value="正常">正常</Select.Option><Select.Option value="禁用">禁用</Select.Option>
                      </Select>
                    ) }
                ]} pagination={false} size="small" />
              <Modal title="添加新用户" open={isUserModalOpen} onCancel={() => setIsUserModalOpen(false)} onOk={async () => {
                  if (!newUser.username || !newUser.pin) { antMessage.warning('用户名和密码不能为空'); return; }
                  const fd = new FormData();
                  fd.append('username', newUser.username); fd.append('pin', newUser.pin); fd.append('real_name', newUser.real_name); fd.append('role', newUser.role); fd.append('department', newUser.department); fd.append('contact', newUser.contact); fd.append('status', newUser.status);
                  const res = await api.post('/users/add', fd);
                  if (res.data.status === 'success') { antMessage.success('添加成功'); setIsUserModalOpen(false); loadUsers(); } else { antMessage.error(res.data.message); }
                }}>
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Input placeholder="用户名" value={newUser.username} onChange={(e) => setNewUser({...newUser, username: e.target.value})} />
                  <Input.Password placeholder="密码" value={newUser.pin} onChange={(e) => setNewUser({...newUser, pin: e.target.value})} />
                  <Input placeholder="姓名" value={newUser.real_name} onChange={(e) => setNewUser({...newUser, real_name: e.target.value})} />
                  <Select placeholder="角色" value={newUser.role} onChange={(val) => setNewUser({...newUser, role: val})} style={{ width: '100%' }}>
                    <Select.Option value="admin">管理员</Select.Option><Select.Option value="manager">经理</Select.Option><Select.Option value="developer">研发人员</Select.Option><Select.Option value="viewer">观察者</Select.Option>
                  </Select>
                  <Input placeholder="部门" value={newUser.department} onChange={(e) => setNewUser({...newUser, department: e.target.value})} />
                  <Input placeholder="联系方式" value={newUser.contact} onChange={(e) => setNewUser({...newUser, contact: e.target.value})} />
                  <Select placeholder="状态" value={newUser.status} onChange={(val) => setNewUser({...newUser, status: val})} style={{ width: '100%' }}>
                    <Select.Option value="正常">正常</Select.Option><Select.Option value="禁用">禁用</Select.Option>
                  </Select>
                </Space>
              </Modal>
            </Spin>
          )}
          {activeView === 'health' && (
            <Spin spinning={healthLoading}>
              {healthData ? (
                <>
                  <h2>系统健康仪表板</h2>
                  <Row gutter={16}>
                    <Col span={12}><Card><Statistic title="总任务数" value={healthData.total_tasks} /></Card></Col>
                    <Col span={12}><Card><Statistic title="成功率" value={healthData.success_rate} suffix="%" /></Card></Col>
                    <Col span={12} style={{ marginTop: 16 }}><Card><Statistic title="活跃用户" value={healthData.active_users} /></Card></Col>
                    <Col span={12} style={{ marginTop: 16 }}><Card><Statistic title="总用户" value={healthData.total_users} /></Card></Col>
                  </Row>
                  <Card title="工具调用分布" style={{ marginTop: 16 }}>
                    <Table dataSource={healthData.sorted_tools.map((item: any, index: number) => ({ ...item, key: index }))} columns={toolColumns} pagination={false} size="small" />
                  </Card>
                </>
              ) : (<p>暂无健康数据</p>)}
            </Spin>
          )}
          {activeView === 'logs' && (
            <Spin spinning={logsLoading}>
              <h2>系统操作日志（不可删除 · 可追溯）</h2>
              <Space style={{ marginBottom: 16 }}>
                <Input placeholder="搜索操作人/窗口、动作或详情..." prefix={<SearchOutlined />} value={logSearch} onChange={(e) => setLogSearch(e.target.value)} allowClear style={{ width: 300 }} />
                <Button icon={<DownloadOutlined />} onClick={handleExportLogs}>导出</Button>
              </Space>
              <Table dataSource={filteredLogs.map((item, index) => ({...item, key: index}))} columns={logColumns} pagination={{ pageSize: 20 }} size="small" scroll={{ y: 400 }} />
            </Spin>
          )}
          {activeView === 'status' && (
            <Spin spinning={statusLoading}>
              <h2>📊 实时 Worker 状态监控</h2>
              <Button onClick={loadStatus} icon={<DownloadOutlined />} style={{ marginBottom: 16 }}>刷新状态</Button>
              <Table dataSource={statusData.map((item, index) => ({ ...item, key: index }))} columns={statusColumns} pagination={false} size="small" rowKey="name" />
            </Spin>
          )}
          {activeView === 'chat' && (
            <>
              {messages.map((msg, idx) => (
                <div key={idx} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', marginBottom: 12 }}>
                  <div style={{ maxWidth: '80%', padding: '10px 16px', borderRadius: 8, background: msg.role === 'user' ? '#1890ff' : '#fff', color: msg.role === 'user' ? '#fff' : '#333', boxShadow: '0 2px 8px rgba(0,0,0,0.1)' }}>
                    {msg.role === 'assistant' ? (
                      <div className="markdown-body" style={{ textAlign: 'left' }}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                          <Button size="small" type="text" icon={<LikeOutlined />} onClick={async () => {
                            const fd = new FormData(); fd.append('session_id', sessionId); fd.append('feedback_type', 'up'); await api.post('/feedback', fd); antMessage.success('感谢您的点赞！');
                          }} />
                          <Button size="small" type="text" danger icon={<DislikeOutlined />} onClick={async () => {
                            const fd = new FormData(); fd.append('session_id', sessionId); fd.append('feedback_type', 'down'); await api.post('/feedback', fd); antMessage.info('感谢您的反馈，我们会努力改进！');
                          }} />
                        </div>
                      </div>
                    ) : (
                      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{msg.content}</div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </>
          )}
        </Content>
        <div style={{ border: '1px solid #4ade80', borderRadius: '12px', margin: '12px 20px 0 20px', padding: '12px', background: '#f5f5f5' }}>
          <div style={{ marginBottom: 8 }}>
            <Input.TextArea value={input} onChange={(e) => setInput(e.target.value)} onPressEnter={handleSend} disabled={loading} placeholder={loading ? "AI 正在处理复杂任务..." : "发消息或按住喇叭说话，松开发送..."} autoSize={{ minRows: 1, maxRows: 4 }} style={{ borderRadius: '8px', fontSize: '16px', border: '1px solid #d9d9d9', background: '#fff' }} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="file" ref={chatFileInputRef} style={{ display: 'none' }} onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  setSelectedFile(file);
                  const reader = new FileReader();
                  reader.onload = (event) => {
                    const content = event.target?.result as string;
                    setPendingFile({ name: file.name, content });
                  };
                  reader.readAsText(file);
                }
                e.target.value = '';
              }} />
            <Tooltip title="上传文件">
              <Button type="text" icon={<PaperClipOutlined />} onClick={() => chatFileInputRef.current?.click()} />
            </Tooltip>
            <Tooltip title="按住说话">
              <Button type="text" icon={<SoundOutlined />} onMouseDown={startRecording} onMouseUp={stopRecording} onMouseLeave={stopRecording} loading={isListening} />
            </Tooltip>
            {selectedFile && (
              <span style={{ marginLeft: 4, color: '#1890ff', fontSize: '14px' }}>
                📎 {selectedFile.name}
                <CloseOutlined style={{ marginLeft: 4, cursor: 'pointer' }} onClick={() => { setSelectedFile(null); setPendingFile(null); }} />
              </span>
            )}
            <Button type="primary" icon={<SendOutlined />} onClick={handleSend} loading={loading} style={{ marginLeft: 'auto' }}>发送</Button>
          </div>
        </div>
        <div style={{ textAlign: 'center', color: '#999', fontSize: '16px', marginTop: '10px', marginBottom: '12px', userSelect: 'none' }}>
          遨游AI星空，尽享AI快乐
        </div>
      </div>
      <div style={{ width: '7cm', flex: '0 0 7cm', background: '#f5f5f5', borderLeft: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column', paddingTop: 16 }}>
        <div style={{ paddingLeft: '12px' }}>
          <div style={{ display: 'flex', justifyContent: 'flex-start', gap: '8px', marginBottom: '8px' }}>
            <Button type={activeView === 'chat' ? 'primary' : 'default'} size="small" onClick={() => setActiveView('chat')}>聊天</Button>
            <Button type={activeView === 'health' ? 'primary' : 'default'} size="small" onClick={loadHealth}>系统健康</Button>
            <Button type={activeView === 'status' ? 'primary' : 'default'} size="small" onClick={loadStatus}>状态监控</Button>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-start', gap: '8px' }}>
            <Button type={activeView === 'logs' ? 'primary' : 'default'} size="small" onClick={loadLogs}>日志</Button>
            <Button type={activeView === 'kb' ? 'primary' : 'default'} size="small" onClick={() => { setActiveView('kb'); loadKbFiles(); }}>知识库</Button>
            {user.role === 'admin' || user.username === 'carol' ? (
              <Button type={activeView === 'admin' ? 'primary' : 'default'} size="small" onClick={() => { setActiveView('admin'); loadUsers(); }}>用户管理</Button>
            ) : null}
          </div>
        </div>
        <div style={{ marginTop: 'auto', marginBottom: 16, paddingRight: '12px', display: 'flex', justifyContent: 'flex-end' }}>
          <Tooltip title="退出登录">
            <Button icon={<LogoutOutlined />} onClick={handleLogout} danger type="text">退出</Button>
          </Tooltip>
        </div>
      </div>
      <Modal title="上传文档到知识库" open={isKbUploadOpen} onCancel={() => { setIsKbUploadOpen(false); setKbFile(null); setKbTags(''); }} onOk={handleKbSubmit} okText="提交索引">
        <div style={{ marginBottom: 12, padding: '10px', border: '1px dashed #d9d9d9', borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span>{kbFile ? kbFile.name : '未选择文件'}</span>
          {kbFile && <CloseOutlined style={{ cursor: 'pointer' }} onClick={() => setKbFile(null)} />}
        </div>
        <Input placeholder="输入标签（用逗号分隔，可选）" value={kbTags} onChange={(e) => setKbTags(e.target.value)} />
      </Modal>
    </Layout>
  );
}