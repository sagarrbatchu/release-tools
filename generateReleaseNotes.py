import subprocess
import jira
import os
import sys
import argparse

jiraURL = 'https://issues.cask.co/'
jiraAgentUsername = 'releaseAgent'


class ReleaseNote():
    """ Class to hold data for a release note """

    def __init__(self, id, releaseNote, issueType):
        self.id = id
        self.releaseNote = releaseNote if releaseNote.endswith('.') else releaseNote+'.'  # Clean up string
        self.issueType = issueType
        self.link = '%sbrowse/%s' % (jiraURL, id)

    def toString(self):
        return '- `%s <%s>`_ - %s' % (self.id, self.link, self.releaseNote)


def createHeader(text):
    """ Create an RST style section header    """

    return ['', text, '='*len(text)]


def parseArgs():
    """ Parse command line arguments """

    parser = argparse.ArgumentParser(
        description='Script for automatically pulling tickets for current release from CDAP JIRA and compile them into a release notes file.')

    parser.add_argument('version',
                        type=str,
                        help='Version string to generate release notes for. Ex. 6.1.4')

    parser.add_argument('-o', '--output',
                        type=str,
                        help='File to output release notes to, defaults to releaseNotes.rst in the current directory')

    parser.add_argument('--passwordProject',
                        type=str,
                        help='Advanced use only. Which GCP project the Jira agent password will be fetched from',
                        default='cloud-data-fusion-builds')

    parser.add_argument('--passwordVersion',
                        type=int,
                        help='Advanced use only. Which version of the Jira agent password will be fetched from the Secret Manager',
                        default=1)

    parser.add_argument('--passwordId',
                        type=str,
                        help='Advanced use only. The ID the Jira agent password in the Secret Manager.',
                        default='JiraPassword')

    args = parser.parse_args()
    return args


def main():
    """ Main function that does all the work """

    # Parse command args and setup constants
    args = parseArgs()
    version = args.version
    issueFilter = 'project in (CDAP, "CDAP Plugins") AND fixVersion = %s AND "Release Notes" is not EMPTY' % version
    issueFields = 'status,resolution,issuetype,Release Notes'

    # Attempt to get JIRA agent password for GCP Secret Manager
    try:
        print("DEBUG: Fetching credentials for JIRA Agent.")

        # Setup commands
        gcloudSetProjectCommand = 'gcloud config set project %s > /dev/null 2>&1' % args.passwordProject
        gcloudGetPasswordCommand = 'gcloud secrets versions access %s --secret="%s"' % (args.passwordVersion, args.passwordId)

        code = subprocess.call(gcloudSetProjectCommand, shell=True)
        # If we could not point gcloud to this project
        if code != 0:
            sys.stderr.write(
                "ERROR: Unable to update gcloud project to %s. Please ensure that this project exists and that you have access.\n" % args.passwordProject)
            return code

        serviceCheck = subprocess.check_output('gcloud services list --filter="secretmanager.googleapis.com" 2>&1', shell=True).decode('utf-8')
        # If the Secret Manager API is not enabled in this project
        # (this check is needed because gcloud will prompt the user to enable it if we try to access the API without it being enabled)
        if '0 items' in serviceCheck:
            sys.stderr.write(
                "ERROR: Secret Manager API is not enabled in project '%s'. This API is required to fetch the credentials for the Jira agent.\n"
                % args.passwordProject)
            return code

        # Fetch the password
        jiraAgentPassword = subprocess.check_output(gcloudGetPasswordCommand, shell=True).decode('utf-8')
    except Exception as e:
        sys.stderr.write("ERROR: '%s' returned an error\n" % gcloudGetPasswordCommand)
        sys.stderr.write(
            "ERROR: Unable to retreive JIRA Agent password from Google Cloud Secret Manager. Ensure correct project, version and password ID are being used.\n")
        return 1

    # Init agent and get search results
    agent = jira.JIRA(jiraURL, auth=(jiraAgentUsername, jiraAgentPassword))
    print("DEBUG: JIRA Agent created successfully!")
    print("DEBUG: Searching for JIRA tickets with 'Fix Version = %s'" % version)
    searchResults = agent.search_issues(issueFilter, maxResults=1000, fields=issueFields, json_result=True)

    print("DEBUG: Found %d issues with release notes for version %s" % (searchResults['total'], version))

    # Release notes grouped by type
    releaseNotes = {'New Feature': [], 'Improvement': [], 'Bug': [], 'Task': [], 'Sub-task': []}
    for issue in searchResults['issues']:

        issueFields = issue['fields']
        note = issueFields['customfield_10300'].strip()
        id = issue['key']

        # Print warnings if the tickets arent marked as Fixed and Closed which they should be at this stage of the release
        if issueFields['resolution'] is None or issueFields['resolution']['name'] != 'Fixed':
            print('WARN: Issue %s is not marked as Fixed!' % id)
        if issueFields['status'] is None or issueFields['status']['name'] != 'Closed':
            print('WARN: Issue %s is not marked as Closed!' % id)

        issueType = issueFields['issuetype']['name']
        if issueType not in releaseNotes:
            releaseNotes[issueType] = []

        # Add ReleaseNote object to dict under correct issueType
        releaseNotes[issueType].append(ReleaseNote(id, note, issueType))

    releaseNotesOrder = ['New Feature', 'Improvement', 'Bug']  # Order that the sections will appear in the doc
    releaseNotesPrettyName = {'New Feature': 'New Features', 'Improvement': 'Improvements', 'Bug': 'Bug Fixes'}  # Better names for each issueType
    contentLines = []
    for issueType in releaseNotesOrder:
        contentLines = contentLines + createHeader(releaseNotesPrettyName[issueType])

        # Sort the issues by their ID so they appear in sorted order in the final doc
        sortedNotes = sorted(releaseNotes[issueType], key=lambda releaseNote: releaseNote.id)
        if len(sortedNotes) == 0:
            contentLines.append("No changes.")
            continue
        for note in sortedNotes:
            contentLines.append(note.toString())

    # Save all results to file
    contentLines = [line+'\n' for line in contentLines]
    filename = 'releaseNotes.rst'
    if args.output:
        filename = args.output
        filename += '.rst' if not filename.endswith('.rst') else ""
    outputFile = open(filename, 'w')
    outputFile.writelines(contentLines)
    outputFile.close()

    print("DEBUG: Done! Generated release notes in file '%s'" % filename)
    return 0


if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)