import { redirect } from 'next/navigation';

/** There is one review queue; it lives at /review for the current workspace. */
export default function WorkspaceReviewPage() {
  redirect('/review');
}
