
/* eslint-disable */

// @ts-nocheck

// noinspection JSUnusedGlobalSymbols

import "@jararaca/core"
import createClassInfiniteQueryHooks
import createClassMutationHooks
import createClassQueryHooks
import HttpBackend
import HttpBackendRequest
import paginationModelByFirstArgPaginationFilter
import recursiveCamelToSnakeCase }
import ResponseType
import { HttpService

function makeFormData(data: Record<string, any>): FormData {
  const formData = new FormData();
  for (const key in data) {
    const value = data[key];
    for (const v of genFormDataValue(value)) {
      formData.append(key, v);
    }
  }
  return formData;
}

function* genFormDataValue(value: any): any {
  if (Array.isArray(value)) {
    // Stringify arrays as JSON
    for (const item of value) {
      // formData.append(`${key}`, item);
      yield* genFormDataValue(item);
    }
  } else if (typeof value === "object" && value.constructor === Object) {
    // Stringify plain objects as JSON
    // formData.append(key, JSON.stringify(value));
    yield JSON.stringify(
      recursiveCamelToSnakeCase(value)
    );
  } else {
    // For primitives (string, number, boolean), append as-is
    yield value;
  }
}
