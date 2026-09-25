export default function ImportSteps({ step }: { step: number }) {
  // A step before the current one is already done — Save posting in
  // particular is a real persistence checkpoint (the role already exists in
  // the database once you're past it), so it must look visibly complete
  // rather than merely "not the active step" once you've moved on.
  return <ol className="workflow-steps" aria-label="Posting workflow">
    {['Save posting', 'Review details', 'Review requirements', 'Compare'].map((label, i) =>
      <li key={label} aria-current={step === i ? 'step' : undefined} className={i < step ? 'completed' : undefined}>
        {i < step ? '✓ ' : `${i + 1}. `}{label}
      </li>)}
  </ol>
}
