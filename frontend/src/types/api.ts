/**
 * Shapes shared with the FastAPI backend's error responses.
 *
 * FastAPI returns { detail: string } for most errors, a list of validation
 * issues for request validation (422), and structured objects for a few
 * domain errors (for example { detail, issues } or { detail, preview }).
 */
export interface ApiValidationIssue {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface ApiErrorBody {
  detail?: string | ApiValidationIssue[];
  [key: string]: unknown;
}
