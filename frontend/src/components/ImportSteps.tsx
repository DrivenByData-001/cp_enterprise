export default function ImportSteps({ step }: { step: number }) {
  return <ol className="workflow-steps" aria-label="Posting workflow">
    {['Add posting', 'Review details', 'Review requirements', 'Compare'].map((label, i) =>
      <li key={label} aria-current={step === i ? 'step' : undefined}>{i + 1}. {label}</li>)}
  </ol>
}
