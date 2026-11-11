import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { BookUser } from 'lucide-react';
import Card from '../common/Card';
import Button from '../common/Button';
import EmptyState from '../common/EmptyState';
import { inputCls } from './fields';
import { createContact, deleteContact, updateContact } from '../../api/kiosks';
import { queryKeys, useContacts } from '../../api/queries';
import { useToast } from '../../hooks/useToast';

export default function ContactsCard() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: contacts = [] } = useContacts();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [editId, setEditId] = useState<string | null>(null);
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: queryKeys.contacts });
  };
  const onError = (e: unknown) =>
    toast.show((e as { response?: { data?: { detail?: string } } }).response?.data?.detail || 'Contact change failed');

  const save = useMutation({
    mutationFn: () => (editId
      ? updateContact(editId, { name: name.trim(), email: email.trim() })
      : createContact({ name: name.trim(), email: email.trim() })),
    meta: { suppressGlobalError: true },
    onSuccess: () => { setName(''); setEmail(''); setEditId(null); invalidate(); },
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteContact(id),
    meta: { suppressGlobalError: true },
    onSuccess: invalidate,
    onError,
  });

  return (
    <Card title="Address book" description="Your personal contacts, used as email targets (kiosks link here).">
      <div className="space-y-4">
        {(
          <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim() && email.trim()) save.mutate(); }}>
            <input aria-label="Contact name" className={`${inputCls} flex-1 min-w-40`} placeholder="Name" value={name}
              onChange={(e) => setName(e.target.value)} />
            <input aria-label="Contact email" type="email" className={`${inputCls} flex-1 min-w-48 font-mono`} placeholder="email@example.com"
              value={email} onChange={(e) => setEmail(e.target.value)} />
            <Button type="submit" disabled={!name.trim() || !email.trim() || save.isPending}>{editId ? 'Save' : 'Add'}</Button>
            {editId && <Button type="button" variant="secondary" onClick={() => { setEditId(null); setName(''); setEmail(''); }}>Cancel</Button>}
          </form>
        )}
        {contacts.length === 0 ? (
          <EmptyState icon={BookUser} title="No contacts yet" />
        ) : (
          <div className="space-y-2">
            {contacts.map((c) => (
              <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 p-3 rounded-lg border border-gray-200 dark:border-gray-700">
                <div>
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{c.name}</div>
                  <div className="font-mono text-xs text-gray-500 dark:text-gray-400">{c.email}</div>
                </div>
                {(
                  <div className="flex gap-2">
                    <Button size="sm" variant="secondary" onClick={() => { setEditId(c.id); setName(c.name); setEmail(c.email); }}>Edit</Button>
                    <Button size="sm" variant="danger-ghost" onClick={() => remove.mutate(c.id)}>Delete</Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
