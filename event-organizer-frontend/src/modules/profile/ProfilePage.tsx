import { type FormEvent, useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { changePassword, getProfile, updateProfile } from './profileApi.ts'

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function ProfilePage() {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [timeZone, setTimeZone] = useState('UTC')
  const [loaded, setLoaded] = useState(false)
  const [profileMsg, setProfileMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    getProfile()
      .then((p) => {
        if (cancelled) return
        setEmail(p.email)
        setName(p.name ?? '')
        setTimeZone(p.time_zone ?? browserTz())
        setLoaded(true)
      })
      .catch(() => {
        if (!cancelled) setProfileMsg({ ok: false, text: 'Не удалось загрузить профиль' })
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleProfileSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setProfileMsg(null)
    try {
      await updateProfile({ name, time_zone: timeZone })
      setProfileMsg({ ok: true, text: 'Профиль сохранён' })
    } catch (err) {
      const text = err instanceof ApiError ? err.message : 'Не удалось сохранить профиль'
      setProfileMsg({ ok: false, text })
    }
  }

  async function handlePasswordSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPwMsg(null)
    if (!oldPassword || !newPassword) {
      setPwMsg({ ok: false, text: 'Заполните все поля' })
      return
    }
    if (newPassword !== confirm) {
      setPwMsg({ ok: false, text: 'Новый пароль и подтверждение не совпадают' })
      return
    }
    try {
      await changePassword({ old_password: oldPassword, new_password: newPassword })
      setPwMsg({ ok: true, text: 'Пароль изменён' })
      setOldPassword('')
      setNewPassword('')
      setConfirm('')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setPwMsg({ ok: false, text: 'Неверный текущий пароль' })
        return
      }
      setPwMsg({ ok: false, text: 'Не удалось изменить пароль' })
    }
  }

  if (!loaded && !profileMsg) {
    return <div className="card">Загрузка…</div>
  }

  return (
    <div>
      <div className="page-head">
        <h1>Профиль</h1>
      </div>

      <form className="section" onSubmit={handleProfileSave}>
        <h2>Профиль</h2>
        <label className="field">
          <span>Имя</span>
          <input type="text" name="name" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Часовой пояс</span>
          <TimeZoneField value={timeZone} onChange={setTimeZone} />
        </label>
        <label className="field">
          <span>Email</span>
          <input type="email" name="email" value={email} readOnly />
        </label>
        <div className="inline-actions">
          <button type="submit">Сохранить профиль</button>
        </div>
        {profileMsg && <p className={profileMsg.ok ? 'ok-text' : 'error-text'}>{profileMsg.text}</p>}
      </form>

      <form className="section" onSubmit={handlePasswordSave}>
        <h2>Пароль</h2>
        <label className="field">
          <span>Текущий пароль</span>
          <input
            type="password"
            name="old_password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Новый пароль</span>
          <input
            type="password"
            name="new_password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Подтверждение</span>
          <input
            type="password"
            name="confirm"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <div className="inline-actions">
          <button type="submit">Сменить пароль</button>
        </div>
        {pwMsg && <p className={pwMsg.ok ? 'ok-text' : 'error-text'}>{pwMsg.text}</p>}
      </form>
    </div>
  )
}
