import { describe, expect, it } from 'vitest'

import { cleanPath, comparisonPath, isUnderPath, isWindowsAbsolutePath } from './path-compare'

describe('isWindowsAbsolutePath', () => {
  it.each(['C:\\Users\\me', 'd:/work', '\\\\server\\share'])('accepts %s', path => {
    expect(isWindowsAbsolutePath(path)).toBe(true)
  })

  it.each(['/home/Me', './out', 'out\\report.html', 'C:relative', 'https://x.com'])('rejects %s', path => {
    expect(isWindowsAbsolutePath(path)).toBe(false)
  })
})

describe('cleanPath', () => {
  it('unifies separators and drops trailing slashes', () => {
    expect(cleanPath('C:\\Repos\\App\\')).toBe('C:/Repos/App')
    expect(cleanPath('  /home/User/repo//  ')).toBe('/home/User/repo')
  })

  it('keeps root rather than collapsing to empty', () => {
    expect(cleanPath('/')).toBe('/')
  })
})

describe('comparisonPath', () => {
  it('folds case for Windows drive and UNC paths only', () => {
    expect(comparisonPath('C:/Repos/App')).toBe('c:/repos/app')
    expect(comparisonPath('//server/Share')).toBe('//server/share')
    expect(comparisonPath('/home/User/Repo')).toBe('/home/User/Repo')
  })
})

describe('isUnderPath', () => {
  it('matches a nested path across separator and case differences', () => {
    expect(isUnderPath('C:\\Repos\\App', 'c:/repos/app/src')).toBe(true)
    expect(isUnderPath('C:/Repos/App/', 'C:\\Repos\\App')).toBe(true)
  })

  it('stays case-sensitive on POSIX', () => {
    expect(isUnderPath('/home/User/repo', '/home/User/repo/src')).toBe(true)
    expect(isUnderPath('/home/User/repo', '/home/User/Repo/src')).toBe(false)
  })

  it('does not treat a sibling with a shared prefix as nested', () => {
    expect(isUnderPath('/repos/app', '/repos/app-retry')).toBe(false)
  })
})
