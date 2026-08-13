import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import NovelListPage from './pages/NovelListPage'
import ChatPage from './pages/ChatPage'
import SettingsPage from './pages/SettingsPage'
import AdminLayout from './pages/AdminLayout'
import AdminNovelListPage from './pages/AdminNovelListPage'
import NovelEditorPage from './pages/NovelEditorPage'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        {/* #53：管理端独立布局（不套全局 Layout），菜单两项各对应独立路由。
            #54：小说列表页 /admin；新建/编辑共用编辑页 /admin/novels/*；LLM 设置独立路由。 */}
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<AdminNovelListPage />} />
          <Route path="novels/new" element={<NovelEditorPage />} />
          <Route path="novels/:id" element={<NovelEditorPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route
          path="*"
          element={
            <Layout>
              <Routes>
                <Route path="/" element={<NovelListPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </Routes>
            </Layout>
          }
        />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
