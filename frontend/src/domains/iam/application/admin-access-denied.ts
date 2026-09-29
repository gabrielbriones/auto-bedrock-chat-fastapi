export class AdminAccessDeniedError extends Error {
  constructor() {
    super('Admin capability is required')
    this.name = 'AdminAccessDeniedError'
  }
}