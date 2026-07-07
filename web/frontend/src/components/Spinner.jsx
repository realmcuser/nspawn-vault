import React from 'react';
import { Loader2 } from 'lucide-react';
import { clsx } from 'clsx';

const Spinner = ({ className }) => (
  <Loader2 className={clsx('animate-spin text-primary', className || 'w-5 h-5')} />
);

export default Spinner;
