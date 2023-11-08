## Exporting ignore rules

### Purpose

Export the list of ignore rules made by an organization in FOSSA

### Pre-requisites

- Ensure you have your [full access API token](https://docs.fossa.com/docs/api-reference) from FOSSA.
- Ensure python is installed as well.

### How to run

Here's a way to run the export and output a csv file:

`python export-ignore-rules.py API-TOKEN_HERE --output csv`

#### Reference

| flag/parameter   | Description |
|---|---|
| access_token | FOSSA Full Access API token |
| `--category`  | Default is `licensing`. Another option is `security`  |
| `--count`  |  Default is `1000 `. Number of exceptions per page |
| `--output`  | Defuault is json. Another option is `csv`  |


### Support

Contact your dedicated customer success team at FOSSA. If you don't have one, then please contact support@fossa.com, and give as much details as possible in terms of feedback/issues.
