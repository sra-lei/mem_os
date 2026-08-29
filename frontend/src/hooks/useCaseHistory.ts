import { useAsync } from './useAsync';
import { casesApi } from '@/api/cases';

export function useCaseHistory(caseDefId: number | string | undefined) {
  return useAsync(
    (signal) =>
      caseDefId != null && caseDefId !== '' ? casesApi.history(caseDefId, signal) : Promise.resolve([]),
    [caseDefId],
  );
}
