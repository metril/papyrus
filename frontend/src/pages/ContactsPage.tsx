import ContactsCard from '../components/kiosks/ContactsCard';

/** Stable URL for the shared address book: editable for admins, read-only otherwise. */
export default function ContactsPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold tracking-tight text-gray-900 dark:text-gray-50">Address book</h2>
      <ContactsCard />
    </div>
  );
}
